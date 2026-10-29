"""
Seed GCash payment submissions in every state the review screen has to show:
pending, approved, rejected, withdrawn and cancelled.

The transitions run through `payment_submissions.services`, so approved
submissions really do create payments and draw the fee balances down, and a
cancelled one really does put them back.
"""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from api.fees.models import Fee
from api.payment_submissions import services as submission_services
from api.payment_submissions.models import PaymentSubmission, PaymentSubmissionItems
from api.rbac.roles import Roles
from api.seeding.support import SeedCommand
from api.students.models import Student
from api.users.models import User

#: Share of students with an outstanding balance who file a submission.
SUBMITTING_SHARE = 0.3

SAMPLE_SCREENSHOT = ["seed/gcash-receipt-sample.png"]

REJECTION_REASONS = [
    "Reference number does not match any GCash transaction.",
    "Screenshot is unreadable.",
    "Amount sent is lower than the declared total.",
]


class Command(SeedCommand):
    seed_name = "payment submissions"
    help = "Create demo GCash submissions across every review state."
    depends_on = ("seed_fees", "seed_users")

    def reset(self, **options):
        PaymentSubmissionItems.objects.all().delete()
        deleted, _ = PaymentSubmission.objects.all().delete()
        self.warn(f"deleted {deleted} payment submission row(s)")

    def _reviewer_for(self, institute_id: int) -> User | None:
        return (
            User.objects.filter(
                institute_id=institute_id,
                groups__name__in=[Roles.CMS_STAFF, Roles.CMS_ADMIN],
            )
            .order_by("id")
            .first()
        )

    def seed(self, **options):
        # Activity rather than reference data - see the note in seed_payments.
        if PaymentSubmission.objects.exists():
            self.info("submissions already present; nothing to do (use --reset)")
            return

        students = [
            student
            for student in Student.objects.select_related("program").order_by("id")
            if Fee.objects.filter(
                student=student,
                status__in=[Fee.FeeStatus.PENDING, Fee.FeeStatus.OVERDUE],
                balance__gt=0,
            ).exists()
        ]
        if not students:
            self.warn("no students with an outstanding balance; run seed_fees first")
            return

        chosen = self.sample(students, int(len(students) * SUBMITTING_SHARE) or 1)
        reviewers: dict[int, User | None] = {}

        counts = {
            "pending": 0,
            "approved": 0,
            "rejected": 0,
            "withdrawn": 0,
            "cancelled": 0,
        }
        sequence = PaymentSubmission.objects.count()

        for student in chosen:
            institute_id = student.program.institute_id
            if institute_id not in reviewers:
                reviewers[institute_id] = self._reviewer_for(institute_id)

            fees = list(
                Fee.objects.filter(
                    student=student,
                    status__in=[Fee.FeeStatus.PENDING, Fee.FeeStatus.OVERDUE],
                    balance__gt=0,
                ).order_by("id")[:2]
            )
            if not fees:
                continue

            sequence += 1
            total = sum((fee.balance for fee in fees), Decimal("0.00"))

            submission = PaymentSubmission.objects.create(
                student=student,
                screenshot=SAMPLE_SCREENSHOT,
                total_amount_paid=total,
                reference_number=f"SEED{sequence:010d}",
                status=PaymentSubmission.SubmissionStatus.PENDING,
            )
            PaymentSubmissionItems.objects.bulk_create(
                [
                    PaymentSubmissionItems(
                        payment_submission=submission,
                        fee=fee,
                        previous_balance=fee.balance,
                        amount_paid=fee.balance,
                    )
                    for fee in fees
                ]
            )

            outcome = self._decide(submission, reviewers[institute_id])
            counts[outcome] += 1

        for state, count in counts.items():
            if count:
                self.created(f"{state} submission", count)

    def _decide(self, submission: PaymentSubmission, reviewer: User | None) -> str:
        """Push a fresh submission into one of the five terminal-ish states."""
        roll = self.rng.random()

        # A student recalling their own submission needs no reviewer.
        if roll < 0.12:
            submission_services.withdraw_submission(
                submission=submission,
                actor=submission.student.user,
                reason="Seeded example: sent from the wrong GCash number.",
            )
            return "withdrawn"

        if reviewer is None or roll < 0.42:
            return "pending"

        if roll < 0.57:
            submission.status = PaymentSubmission.SubmissionStatus.REJECTED
            submission.reviewed_by = reviewer
            submission.reviewed_at = timezone.now()
            submission.remarks = self.pick(REJECTION_REASONS)
            submission.save()
            return "rejected"

        submission_services.approve_submission(
            submission=submission,
            actor=reviewer,
            remarks="Seeded example: reference number verified.",
        )

        if roll > 0.95:
            submission_services.cancel_submission(
                submission=submission,
                actor=reviewer,
                reason="Seeded example: duplicate of an earlier submission.",
            )
            return "cancelled"

        return "approved"
