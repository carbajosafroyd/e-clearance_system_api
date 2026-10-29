"""Payment lifecycle tests: deduction correctness and cancellation.

Money rows are immutable - corrections are reversals. These tests pin the
rules from `in_consideration/cancellation-and-status-design.md`: every
deduction goes through the locked service, cancellation restores the fee's
balance, and the old mutation paths (PUT/PATCH, hard delete) are gone.
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
from api.payment_submissions.models import PaymentSubmission

from .models import Payment


class PaymentTestBase(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        self.student = make_student(self.program)
        self.staff = make_staff(self.institute, superuser=True)
        self.fee = make_fee(self.student, self.category, amount="100.00")
        self.client.force_authenticate(user=self.staff)

    def pay(self, fee, amount, **extra):
        return self.client.post(
            reverse("payments-list"),
            {"fee": fee.pk, "amount_paid": amount, **extra},
            format="json",
        )


class PaymentCreateTests(PaymentTestBase):
    def test_payment_deducts_balance_and_records_previous_balance(self):
        response = self.pay(self.fee, "60.00")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("40.00"))
        self.assertEqual(self.fee.status, Fee.FeeStatus.PENDING)

        payment = Payment.objects.get()
        self.assertEqual(payment.previous_balance, Decimal("100.00"))
        self.assertEqual(payment.received_by, self.staff)
        self.assertEqual(payment.status, Payment.PaymentStatus.ACTIVE)

    def test_exact_payment_settles_the_fee(self):
        response = self.pay(self.fee, "100.00")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("0.00"))
        self.assertEqual(self.fee.status, Fee.FeeStatus.PAID)

    def test_sub_peso_remainder_is_not_silently_forgiven(self):
        """Regression: `if fee.balance < 1` used to zero out anything under
        a peso and mark the fee PAID."""
        response = self.pay(self.fee, "99.60")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("0.40"))
        self.assertEqual(self.fee.status, Fee.FeeStatus.PENDING)

    def test_waived_fees_do_not_accept_payments(self):
        self.fee.status = Fee.FeeStatus.WAIVED
        self.fee.save(update_fields=["status"])

        response = self.pay(self.fee, "50.00")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Payment.objects.count(), 0)

    def test_overpayment_is_rejected(self):
        response = self.pay(self.fee, "100.01")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("100.00"))

    def test_payment_sends_the_student_a_detailed_receipt(self):
        self.pay(self.fee, "60.00")
        payment = Payment.objects.get()

        notification = Notification.objects.get(user=self.student.user)
        self.assertEqual(notification.type, "payment_received")
        self.assertEqual(notification.sender, self.staff)
        # The receipt shows the amount, the balance it landed on, and what
        # remains.
        self.assertIn(f"Payment No.: {payment.pk}", notification.message)
        self.assertIn("₱60.00", notification.message)
        self.assertIn("₱100.00", notification.message)  # balance before
        self.assertIn("₱40.00", notification.message)  # balance after
        self.assertIn(self.category.category_name, notification.message)

        data = notification.data
        self.assertEqual(data["payment_id"], payment.pk)
        self.assertEqual(data["amount_paid"], "60.00")
        self.assertEqual(data["previous_balance"], "100.00")
        self.assertEqual(data["new_balance"], "40.00")
        self.assertEqual(data["fee_status"], Fee.FeeStatus.PENDING)

    def test_settling_receipt_celebrates_the_fully_paid_fee(self):
        self.pay(self.fee, "100.00")

        notification = Notification.objects.get(user=self.student.user)
        self.assertIn("fully paid", notification.message)
        self.assertEqual(notification.data["fee_status"], Fee.FeeStatus.PAID)


class PaymentBulkCreateTests(PaymentTestBase):
    def test_same_fee_twice_deducts_both_amounts(self):
        """Regression: each bulk item used to resolve its own Fee copy from
        the same starting balance, so the last write won."""
        response = self.client.post(
            reverse("payments-bulk-create"),
            {
                "payments": [
                    {"fee": self.fee.pk, "amount_paid": "60.00"},
                    {"fee": self.fee.pk, "amount_paid": "40.00"},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("0.00"))
        self.assertEqual(self.fee.status, Fee.FeeStatus.PAID)
        self.assertEqual(Payment.objects.count(), 2)
        # The second payment saw the balance the first one left behind.
        second = Payment.objects.order_by("pk").last()
        self.assertEqual(second.previous_balance, Decimal("40.00"))

    def test_bulk_sends_one_receipt_per_payment(self):
        self.client.post(
            reverse("payments-bulk-create"),
            {
                "payments": [
                    {"fee": self.fee.pk, "amount_paid": "60.00"},
                    {"fee": self.fee.pk, "amount_paid": "40.00"},
                ]
            },
            format="json",
        )

        receipts = Notification.objects.filter(
            user=self.student.user, type="payment_received"
        ).order_by("pk")
        self.assertEqual(receipts.count(), 2)
        # Each receipt shows the balance its own payment landed on.
        first, second = receipts
        self.assertEqual(first.data["previous_balance"], "100.00")
        self.assertEqual(first.data["new_balance"], "40.00")
        self.assertEqual(second.data["previous_balance"], "40.00")
        self.assertEqual(second.data["new_balance"], "0.00")
        self.assertEqual(second.data["fee_status"], Fee.FeeStatus.PAID)

    def test_bulk_that_overshoots_one_fee_rolls_back_entirely(self):
        response = self.client.post(
            reverse("payments-bulk-create"),
            {
                "payments": [
                    {"fee": self.fee.pk, "amount_paid": "60.00"},
                    {"fee": self.fee.pk, "amount_paid": "50.00"},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("100.00"))
        self.assertEqual(Payment.objects.count(), 0)


class PaymentCancelTests(PaymentTestBase):
    def cancel(self, payment, reason="typo in the amount"):
        return self.client.post(
            reverse("payments-cancel", args=[payment.pk]),
            {"reason": reason},
            format="json",
        )

    def test_cancel_restores_the_balance_and_reopens_a_paid_fee(self):
        self.pay(self.fee, "100.00")
        payment = Payment.objects.get()

        response = self.cancel(payment)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.PaymentStatus.CANCELLED)
        self.assertEqual(payment.cancelled_by, self.staff)
        self.assertIsNotNone(payment.cancelled_at)
        self.assertEqual(payment.cancellation_reason, "typo in the amount")

        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("100.00"))
        self.assertEqual(self.fee.status, Fee.FeeStatus.PENDING)

    def test_cancel_requires_a_reason(self):
        self.pay(self.fee, "50.00")
        payment = Payment.objects.get()

        response = self.client.post(
            reverse("payments-cancel", args=[payment.pk]), {}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.PaymentStatus.ACTIVE)

    def test_cancel_twice_is_refused(self):
        self.pay(self.fee, "50.00")
        payment = Payment.objects.get()
        self.cancel(payment)

        response = self.cancel(payment)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.fee.refresh_from_db()
        # The balance was restored once, not twice.
        self.assertEqual(self.fee.balance, Decimal("100.00"))

    def test_cancel_notifies_the_student_with_the_full_story(self):
        self.pay(self.fee, "100.00")
        payment = Payment.objects.get()

        self.cancel(payment)

        notification = Notification.objects.get(
            user=self.student.user, type="payment_cancelled"
        )
        self.assertEqual(notification.sender, self.staff)
        # The message identifies the payment, the reason, the balance it
        # restored, and reassures the student the amount came back.
        self.assertIn(f"Payment No.: {payment.pk}", notification.message)
        self.assertIn("₱100.00", notification.message)
        self.assertIn("typo in the amount", notification.message)
        self.assertIn("returned to your fee balance", notification.message)
        self.assertIn(self.category.category_name, notification.message)

        data = notification.data
        self.assertEqual(data["payment_id"], payment.pk)
        self.assertEqual(data["fee_id"], self.fee.pk)
        self.assertEqual(data["amount_returned"], "100.00")
        self.assertEqual(data["restored_balance"], "100.00")
        self.assertEqual(data["fee_status"], Fee.FeeStatus.PENDING)
        self.assertEqual(data["cancellation_reason"], "typo in the amount")

    def test_cancel_still_succeeds_for_a_student_without_an_account(self):
        orphan = make_student(self.program, with_user=False)
        fee = make_fee(orphan, self.category, amount="50.00")
        self.pay(fee, "50.00")
        payment = Payment.objects.get()

        response = self.cancel(payment)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("50.00"))
        self.assertEqual(Notification.objects.count(), 0)

    def test_submission_born_payment_cannot_be_cancelled_alone(self):
        submission = PaymentSubmission.objects.create(
            student=self.student,
            screenshot=[],
            total_amount_paid=Decimal("50.00"),
            reference_number="REF-1",
            status=PaymentSubmission.SubmissionStatus.APPROVED,
        )
        payment = Payment.objects.create(
            fee=self.fee,
            amount_paid=Decimal("50.00"),
            previous_balance=Decimal("100.00"),
            payment_method=Payment.PaymentMethod.GCASH,
            payment_submission=submission,
            received_by=self.staff,
        )

        response = self.cancel(payment)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("submission", str(response.data["detail"]).lower())

    def test_students_cannot_cancel(self):
        self.pay(self.fee, "50.00")
        payment = Payment.objects.get()
        self.client.force_authenticate(user=self.student.user)

        response = self.cancel(payment)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PaymentImmutabilityTests(PaymentTestBase):
    def test_put_and_patch_are_gone(self):
        self.pay(self.fee, "50.00")
        payment = Payment.objects.get()
        url = reverse("payments-detail", args=[payment.pk])

        self.assertEqual(
            self.client.put(url, {"amount_paid": "10.00"}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        self.assertEqual(
            self.client.patch(url, {"amount_paid": "10.00"}, format="json").status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_delete_is_superuser_only(self):
        self.pay(self.fee, "50.00")
        payment = Payment.objects.get()
        plain_staff = make_staff(self.institute)
        self.client.force_authenticate(user=plain_staff)

        response = self.client.delete(
            reverse("payments-detail", args=[payment.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())
