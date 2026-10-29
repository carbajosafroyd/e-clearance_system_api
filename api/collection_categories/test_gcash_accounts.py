"""
The student side of the collecting-account list.

What a student is offered, that it narrows by number, and that each account
says how often *this* student has already paid into it - approved
submissions only, since a pending or rejected one is not money that arrived.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from api.collection_categories.models import InstituteGcashAccount
from api.fees.tests.factories import make_institute, make_program, make_student
from api.payment_submissions.models import PaymentSubmission

Status = PaymentSubmission.SubmissionStatus
_seq = iter(range(1, 10_000))


def make_account(institute, number, *, hint="Ju** D***", status=None):
    return InstituteGcashAccount.objects.create(
        institute=institute,
        number=number,
        hint=hint,
        status=status or InstituteGcashAccount.GcashAccountStatus.AVAILABLE,
        gcash_qr=f"gcash_qr/{number}",
    )


def make_submission(student, number, status):
    return PaymentSubmission.objects.create(
        student=student,
        screenshot=[],
        total_amount_paid=Decimal("50.00"),
        reference_number=f"REF-{next(_seq)}",
        status=status,
        gcash_number=number,
    )


class StudentGcashAccountListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.student = make_student(cls.program)
        cls.other = make_student(cls.program)

        cls.first = make_account(cls.institute, "09170000001")
        cls.second = make_account(cls.institute, "09170000002")
        cls.disabled = make_account(
            cls.institute,
            "09170000003",
            status=InstituteGcashAccount.GcashAccountStatus.DISABLED,
        )

        make_submission(cls.student, cls.first.number, Status.APPROVED)
        make_submission(cls.student, cls.first.number, Status.APPROVED)
        # Not money that arrived - must not count.
        make_submission(cls.student, cls.first.number, Status.PENDING)
        make_submission(cls.student, cls.first.number, Status.REJECTED)
        make_submission(cls.student, cls.second.number, Status.APPROVED)
        # Somebody else's history is not this student's.
        make_submission(cls.other, cls.second.number, Status.APPROVED)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.student.user)

    def rows(self, **params):
        response = self.client.get("/api/v1/institute-gcash-accounts/", params)
        self.assertEqual(response.status_code, 200, response.data) # type: ignore
        return {row["number"]: row for row in response.data["data"]} # type: ignore

    def test_only_available_accounts_are_offered(self):
        self.assertEqual(
            set(self.rows()), {self.first.number, self.second.number}
        )

    def test_times_used_counts_only_this_students_approved_submissions(self):
        rows = self.rows()
        self.assertEqual(rows[self.first.number]["times_used"], 2)
        self.assertEqual(rows[self.second.number]["times_used"], 1)

    def test_an_account_never_paid_into_reads_zero(self):
        fresh = make_account(self.institute, "09170000004")
        self.assertEqual(self.rows()[fresh.number]["times_used"], 0)

    def test_the_list_narrows_by_number(self):
        self.assertEqual(set(self.rows(search="0002")), {self.second.number})
