"""The fee status state machine: waive, unwaive, and the locked-down update.

Waiving forgives the remaining balance - it never touches money already
received. Statuses move only through dedicated actions and automation; the
generic update endpoint can no longer set balance, status or total_amount.
"""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ..models import Fee
from .factories import (
    ACADEMIC_YEAR,
    SEMESTER,
    make_category,
    make_fee,
    make_institute,
    make_program,
    make_staff,
    make_student,
)


class FeeStatusActionTestBase(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        self.student = make_student(self.program)
        self.admin = make_staff(self.institute, superuser=True)
        self.client.force_authenticate(user=self.admin)

    def waive(self, fee, remarks="scholarship granted"):
        payload = {"remarks": remarks} if remarks else {}
        return self.client.post(
            reverse("fees-waive", args=[fee.pk]), payload, format="json"
        )

    def unwaive(self, fee):
        return self.client.post(reverse("fees-unwaive", args=[fee.pk]))


class WaiveTests(FeeStatusActionTestBase):
    def test_waive_changes_status_and_keeps_the_balance(self):
        fee = make_fee(self.student, self.category, amount="100.00")

        response = self.waive(fee)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        fee.refresh_from_db()
        self.assertEqual(fee.status, Fee.FeeStatus.WAIVED)
        self.assertEqual(fee.remarks, "scholarship granted")
        # Waive forgives the balance, it does not zero it - the reports
        # exclude waived fees from money math instead.
        self.assertEqual(fee.balance, Decimal("100.00"))

    def test_waive_requires_remarks(self):
        fee = make_fee(self.student, self.category, amount="100.00")

        response = self.waive(fee, remarks="")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        fee.refresh_from_db()
        self.assertEqual(fee.status, Fee.FeeStatus.PENDING)

    def test_overdue_fees_can_be_waived(self):
        fee = make_fee(
            self.student,
            self.category,
            amount="100.00",
            status=Fee.FeeStatus.OVERDUE,
        )

        response = self.waive(fee)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        fee.refresh_from_db()
        self.assertEqual(fee.status, Fee.FeeStatus.WAIVED)

    def test_a_settled_fee_cannot_be_waived(self):
        """Balance 0 means paid - there is nothing to forgive."""
        fee = make_fee(self.student, self.category, amount="100.00")
        fee.balance = Decimal("0.00")
        fee.status = Fee.FeeStatus.PAID
        fee.save(update_fields=["balance", "status"])

        response = self.waive(fee)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_waived_fee_cannot_be_waived_again(self):
        fee = make_fee(
            self.student,
            self.category,
            amount="100.00",
            status=Fee.FeeStatus.WAIVED,
        )

        response = self.waive(fee)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class UnwaiveTests(FeeStatusActionTestBase):
    def test_unwaive_returns_the_fee_to_pending(self):
        fee = make_fee(
            self.student,
            self.category,
            amount="100.00",
            status=Fee.FeeStatus.WAIVED,
        )

        response = self.unwaive(fee)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        fee.refresh_from_db()
        self.assertEqual(fee.status, Fee.FeeStatus.PENDING)
        self.assertEqual(fee.balance, Decimal("100.00"))

    def test_only_waived_fees_can_be_unwaived(self):
        fee = make_fee(self.student, self.category, amount="100.00")

        response = self.unwaive(fee)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LockedDownWriteTests(FeeStatusActionTestBase):
    def test_update_can_no_longer_touch_balance_status_or_total(self):
        fee = make_fee(self.student, self.category, amount="100.00")

        response = self.client.patch(
            reverse("fees-detail", args=[fee.pk]),
            {
                "balance": "0.00",
                "status": Fee.FeeStatus.PAID,
                "total_amount": "5.00",
                "remarks": "still allowed",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("100.00"))
        self.assertEqual(fee.status, Fee.FeeStatus.PENDING)
        self.assertEqual(fee.total_amount, Decimal("100.00"))
        self.assertEqual(fee.remarks, "still allowed")

    def test_create_ignores_a_supplied_status(self):
        response = self.client.post(
            reverse("fees-list"),
            {
                "student": self.student.pk,
                "category": self.category.pk,
                "total_amount": "450.00",
                "status": Fee.FeeStatus.WAIVED,
                "academic_year": ACADEMIC_YEAR,
                "semester": SEMESTER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        fee = Fee.objects.get()
        self.assertEqual(fee.status, Fee.FeeStatus.PENDING)
