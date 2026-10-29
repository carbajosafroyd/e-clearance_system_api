"""Submission lifecycle tests: approve and cancel move money correctly.

Approve is the only path that turns submission items into payments, and it
deducts each fee exactly once, under lock. Cancel is the only way to undo an
approval: it reverses the payments and restores every balance. Withdraw is
the student's own exit, open only while the submission is still pending -
after that, undoing is staff business. Rejected, cancelled, and withdrawn
are terminal.
"""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from api.fees.models import Fee
from api.fees.tests.factories import (
    make_category,
    make_fee,
    make_institute,
    make_program,
    make_staff,
    make_student,
)
from api.notifications.models import Notification
from api.payments.models import Payment
from api.system_settings.models import SystemSetting
from core import setting_buckets

from .models import PaymentSubmission, PaymentSubmissionItems

# The router registers the viewset under this (misspelled) basename.
BASE = "payment-submissins"


class SubmissionTestBase(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        self.student = make_student(self.program)
        self.staff = make_staff(self.institute)
        self.client.force_authenticate(user=self.staff)

    def make_submission(self, items, *, status_=None):
        """items: list of (fee, amount) pairs."""
        submission = PaymentSubmission.objects.create(
            student=self.student,
            screenshot=[],
            total_amount_paid=sum(Decimal(amount) for _, amount in items)
            or Decimal("1.00"),
            reference_number="REF-1",
            status=status_ or PaymentSubmission.SubmissionStatus.PENDING,
        )
        for fee, amount in items:
            PaymentSubmissionItems.objects.create(
                payment_submission=submission,
                fee=fee,
                previous_balance=fee.balance,
                amount_paid=Decimal(amount),
            )
        return submission

    def approve(self, submission, remarks=""):
        return self.client.post(
            reverse(f"{BASE}-approve", args=[submission.pk]),
            {"remarks": remarks},
            format="json",
        )

    def cancel(self, submission, remarks="encoded against the wrong student"):
        return self.client.post(
            reverse(f"{BASE}-cancel", args=[submission.pk]),
            {"remarks": remarks} if remarks else {},
            format="json",
        )


class ApproveTests(SubmissionTestBase):
    def test_approve_creates_one_payment_per_item_and_deducts_once(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])

        response = self.approve(submission)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.APPROVED
        )
        self.assertEqual(submission.reviewed_by, self.staff)

        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("40.00"))

        payment = Payment.objects.get()
        self.assertEqual(payment.payment_submission, submission)
        self.assertEqual(payment.payment_method, Payment.PaymentMethod.GCASH)
        self.assertEqual(payment.previous_balance, Decimal("100.00"))

    def test_two_items_for_the_same_fee_both_deduct(self):
        """Regression: the approve view used to deduct on its own stale copy
        of the fee after the serializer had already deducted, so a second
        item for the same fee clobbered the first deduction."""
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00"), (fee, "40.00")])

        response = self.approve(submission)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("0.00"))
        self.assertEqual(fee.status, Fee.FeeStatus.PAID)
        self.assertEqual(Payment.objects.count(), 2)

        # Each item recorded the balance its money actually landed on.
        first, second = submission.payment_submission_list.order_by("pk")
        self.assertEqual(first.previous_balance, Decimal("100.00"))
        self.assertEqual(second.previous_balance, Decimal("40.00"))

    def test_items_that_overshoot_roll_back_the_whole_approval(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00"), (fee, "50.00")])

        response = self.approve(submission)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("100.00"))
        self.assertEqual(Payment.objects.count(), 0)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.PENDING
        )

    def test_a_submission_stripped_of_its_items_cannot_be_approved(self):
        """Items CASCADE-delete with their fee; approving the empty shell
        used to be a silent no-op."""
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])
        fee.delete()  # takes the item with it

        response = self.approve(submission)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.PENDING
        )

    def test_approve_sends_the_student_a_detailed_receipt(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])

        self.approve(submission, remarks="verified against GCash")

        notification = Notification.objects.get(
            user=self.student.user, type="payment_submission_approved"
        )
        self.assertEqual(notification.sender, self.staff)
        # The receipt identifies the submission and itemizes where the
        # money went, balance by balance.
        self.assertIn("Ref. No. REF-1", notification.message)
        self.assertIn(f"Submission No.: {submission.pk}", notification.message)
        self.assertIn("₱60.00", notification.message)
        self.assertIn("₱100.00", notification.message)  # balance before
        self.assertIn("₱40.00", notification.message)  # balance after
        self.assertIn("verified against GCash", notification.message)
        self.assertIn(self.category.category_name, notification.message)

        data = notification.data
        self.assertEqual(data["submission_id"], submission.pk)
        self.assertEqual(data["total_applied"], "60.00")
        self.assertEqual(len(data["payments_applied"]), 1)
        row = data["payments_applied"][0]
        self.assertEqual(row["fee_id"], fee.pk)
        self.assertEqual(row["previous_balance"], "100.00")
        self.assertEqual(row["new_balance"], "40.00")

    def test_approve_receipt_shows_each_items_own_balances(self):
        """Two items on one fee: each receipt line shows the balance its
        own payment left behind, not the batch's final figure."""
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00"), (fee, "40.00")])

        self.approve(submission)

        notification = Notification.objects.get(
            user=self.student.user, type="payment_submission_approved"
        )
        rows = notification.data["payments_applied"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["previous_balance"], "100.00")
        self.assertEqual(rows[0]["new_balance"], "40.00")
        self.assertEqual(rows[1]["previous_balance"], "40.00")
        self.assertEqual(rows[1]["new_balance"], "0.00")
        self.assertEqual(notification.data["total_applied"], "100.00")

    def test_only_pending_submissions_can_be_approved(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission(
            [(fee, "60.00")],
            status_=PaymentSubmission.SubmissionStatus.REJECTED,
        )

        response = self.approve(submission)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Payment.objects.count(), 0)


class RejectTests(SubmissionTestBase):
    def reject(self, submission, remarks="reference number does not exist"):
        return self.client.post(
            reverse(f"{BASE}-reject", args=[submission.pk]),
            {"remarks": remarks},
            format="json",
        )

    def test_reject_tells_the_student_why_and_that_nothing_was_deducted(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])

        response = self.reject(submission)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification = Notification.objects.get(
            user=self.student.user, type="payment_submission_rejected"
        )
        self.assertEqual(notification.sender, self.staff)
        self.assertIn("Ref. No. REF-1", notification.message)
        self.assertIn("reference number does not exist", notification.message)
        self.assertIn("balances are unchanged", notification.message)
        self.assertIn("your money is not lost", notification.message)
        self.assertIn(self.category.category_name, notification.message)

        data = notification.data
        self.assertEqual(data["submission_id"], submission.pk)
        self.assertEqual(data["reason"], "reference number does not exist")
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["fee_id"], fee.pk)
        self.assertEqual(data["items"][0]["amount"], "60.00")


class CancelTests(SubmissionTestBase):
    def approved_submission(self, *fee_amounts):
        fees = [
            make_fee(self.student, self.category, amount=amount)
            for amount in fee_amounts
        ]
        submission = self.make_submission(
            [(fee, str(fee.balance)) for fee in fees]
        )
        self.approve(submission)
        submission.refresh_from_db()
        return submission, fees

    def test_cancel_reverses_every_payment_and_restores_every_balance(self):
        submission, fees = self.approved_submission("100.00", "250.00")
        for fee in fees:
            fee.refresh_from_db()
            self.assertEqual(fee.status, Fee.FeeStatus.PAID)

        response = self.cancel(submission)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.CANCELLED
        )
        self.assertEqual(submission.cancelled_by, self.staff)
        self.assertIsNotNone(submission.cancelled_at)
        # The review stays as history.
        self.assertEqual(submission.reviewed_by, self.staff)
        self.assertIsNotNone(submission.reviewed_at)

        for fee, amount in zip(fees, ("100.00", "250.00")):
            fee.refresh_from_db()
            self.assertEqual(fee.balance, Decimal(amount))
            self.assertEqual(fee.status, Fee.FeeStatus.PENDING)

        for payment in Payment.objects.all():
            self.assertEqual(payment.status, Payment.PaymentStatus.CANCELLED)
            self.assertEqual(payment.cancelled_by, self.staff)

    def test_cancel_notifies_the_student_with_the_full_story(self):
        submission, fees = self.approved_submission("100.00", "250.00")

        self.cancel(submission)

        notification = Notification.objects.get(
            user=self.student.user, type="payment_submission_cancelled"
        )
        self.assertEqual(notification.sender, self.staff)
        # The message identifies the submission, itemizes every reversed
        # payment with its restored balance, and reassures the student the
        # full total came back.
        self.assertIn("Ref. No. REF-1", notification.message)
        self.assertIn(f"Submission No.: {submission.pk}", notification.message)
        self.assertIn("₱100.00", notification.message)
        self.assertIn("₱250.00", notification.message)
        self.assertIn("₱350.00", notification.message)
        self.assertIn("encoded against the wrong student", notification.message)
        self.assertIn("returned to your fee balance", notification.message)

        data = notification.data
        self.assertEqual(data["submission_id"], submission.pk)
        self.assertEqual(data["reference_number"], "REF-1")
        self.assertEqual(data["total_returned"], "350.00")
        self.assertEqual(len(data["cancelled_payments"]), 2)
        by_fee = {row["fee_id"]: row for row in data["cancelled_payments"]}
        for fee, amount in zip(fees, ("100.00", "250.00")):
            row = by_fee[fee.pk]
            self.assertEqual(row["amount_returned"], amount)
            self.assertEqual(row["restored_balance"], amount)
            self.assertEqual(row["fee_status"], Fee.FeeStatus.PENDING)

    def test_cancel_requires_remarks(self):
        submission, _ = self.approved_submission("100.00")

        response = self.cancel(submission, remarks="")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.APPROVED
        )

    def test_cancel_twice_is_refused_and_restores_only_once(self):
        submission, fees = self.approved_submission("100.00")
        self.cancel(submission)

        response = self.cancel(submission)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        fees[0].refresh_from_db()
        self.assertEqual(fees[0].balance, Decimal("100.00"))

    def test_pending_submissions_cannot_be_cancelled(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])

        response = self.cancel(submission)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class WithdrawTests(SubmissionTestBase):
    def withdraw(self, submission, remarks=None):
        return self.client.post(
            reverse(f"{BASE}-withdraw", args=[submission.pk]),
            {"remarks": remarks} if remarks is not None else {},
            format="json",
        )

    def test_a_student_can_withdraw_their_own_pending_submission(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])
        self.client.force_authenticate(user=self.student.user)

        response = self.withdraw(submission, remarks="picked the wrong fee")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.WITHDRAWN
        )
        # The withdrawal record is the student's own doing; no review ever
        # happened and no money moved.
        self.assertEqual(submission.cancelled_by, self.student.user)
        self.assertIsNotNone(submission.cancelled_at)
        self.assertEqual(submission.cancellation_reason, "picked the wrong fee")
        self.assertIsNone(submission.reviewed_by)
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("100.00"))
        self.assertEqual(Payment.objects.count(), 0)

    def test_withdrawal_remarks_are_optional(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])
        self.client.force_authenticate(user=self.student.user)

        response = self.withdraw(submission)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.WITHDRAWN
        )

    def test_an_approved_submission_cannot_be_withdrawn(self):
        """Once staff accepted it the money moved - undoing that is the
        staff cancel action's job, not the student's."""
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])
        self.approve(submission)
        self.client.force_authenticate(user=self.student.user)

        response = self.withdraw(submission)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.APPROVED
        )
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("40.00"))

    def test_staff_cannot_use_withdraw(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])

        response = self.withdraw(submission)  # authenticated as staff

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.PENDING
        )

    def test_a_student_cannot_withdraw_another_students_submission(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])
        other_student = make_student(self.program)
        self.client.force_authenticate(user=other_student.user)

        response = self.withdraw(submission)

        # The queryset scopes students to their own submissions.
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        submission.refresh_from_db()
        self.assertEqual(
            submission.status, PaymentSubmission.SubmissionStatus.PENDING
        )

    def test_a_withdrawn_submission_cannot_be_approved(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "60.00")])
        self.client.force_authenticate(user=self.student.user)
        self.withdraw(submission)
        self.client.force_authenticate(user=self.staff)

        response = self.approve(submission)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Payment.objects.count(), 0)
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("100.00"))


class DeletionGuardTests(SubmissionTestBase):
    def test_approved_submissions_cannot_be_deleted_by_staff(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "100.00")])
        self.approve(submission)

        response = self.client.delete(
            reverse(f"{BASE}-detail", args=[submission.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(
            PaymentSubmission.objects.filter(pk=submission.pk).exists()
        )

    def test_pending_submissions_are_still_deletable(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        submission = self.make_submission([(fee, "100.00")])

        response = self.client.delete(
            reverse(f"{BASE}-detail", args=[submission.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)


class DecimalCapacityTests(SubmissionTestBase):
    def test_submissions_over_ten_thousand_pesos_are_storable(self):
        """Regression: total_amount_paid and both item amounts were
        max_digits=6, capping every submission at 9,999.99."""
        fee = make_fee(self.student, self.category, amount="15000.00")
        submission = self.make_submission([(fee, "15000.00")])

        response = self.approve(submission)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("0.00"))
        self.assertEqual(fee.status, Fee.FeeStatus.PAID)


class OnlinePaymentsSwitchTests(SubmissionTestBase):
    """
    The desk closes the GCash lane with one setting; the endpoint refuses
    before it does any work, and an institute with no row is left open.
    """

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.student.user)

    def test_a_submission_is_refused_while_online_payments_are_off(self):
        SystemSetting.objects.create(
            institute=self.institute,
            setting_key=setting_buckets.ONLINE_PAYMENTS_KEY,
            setting_value="false",
        )

        response = self.client.post(reverse(f"{BASE}-list"), {}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "online_payments_off")
        self.assertFalse(PaymentSubmission.objects.exists())

    def test_no_row_means_the_lane_is_open(self):
        # Refused on the payload, not on the switch: the guard let it through
        # to the serializer, which is where an empty submission belongs.
        response = self.client.post(reverse(f"{BASE}-list"), {}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reopening_the_lane_lets_submissions_through_again(self):
        row = SystemSetting.objects.create(
            institute=self.institute,
            setting_key=setting_buckets.ONLINE_PAYMENTS_KEY,
            setting_value="false",
        )
        row.setting_value = "true"
        row.save(update_fields=["setting_value"])

        response = self.client.post(reverse(f"{BASE}-list"), {}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ProofIsImmutableTests(SubmissionTestBase):
    """
    A submission is the student's claim and the evidence an approval was
    judged on, so nothing about the proof may change after it is sent. The
    officer's own remarks are the one exception.
    """

    def test_reference_number_cannot_be_edited(self):
        submission = self.make_submission([])
        response = self.client.patch(
            reverse(f"{BASE}-detail", args=[submission.pk]),
            {"reference_number": "REF-2"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be edited", str(response.data))
        submission.refresh_from_db()
        self.assertEqual(submission.reference_number, "REF-1")

    def test_images_and_amount_cannot_be_edited(self):
        submission = self.make_submission([])
        for payload in ({"screenshot": []}, {"total_amount_paid": "999.00"}, {"status": "approved"}):
            response = self.client.patch(
                reverse(f"{BASE}-detail", args=[submission.pk]), payload, format="json"
            )
            self.assertEqual(response.status_code, 400, payload)

    def test_remarks_can_still_be_edited(self):
        submission = self.make_submission([])
        response = self.client.patch(
            reverse(f"{BASE}-detail", args=[submission.pk]),
            {"remarks": "Checked against the GCash statement."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        submission.refresh_from_db()
        self.assertEqual(submission.remarks, "Checked against the GCash statement.")
        self.assertEqual(submission.reference_number, "REF-1")
