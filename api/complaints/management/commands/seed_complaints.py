"""
Seed a realistic complaints queue.

Everything runs through `complaints.services`, so the resolved and rejected
rows also carry the notification the student would really have received -
which is what makes the seeded notification list worth looking at.
"""

from __future__ import annotations

from api.complaints import services
from api.complaints.models import Complaint, ComplaintCategory
from api.fees.models import Fee
from api.institutes.models import Institute
from api.rbac.roles import Roles
from api.seeding.support import SeedCommand
from api.students.models import Student
from api.users.models import User
from core import features
from core.choices import SystemScope

#: (system, category, title, description, wants_fee)
#:
#: The billing ones ask for a fee to be attached, because "this fee is wrong"
#: with no fee named is exactly the complaint nobody can act on - the seeded
#: data should show the good version.
COMPLAINTS = [
    (
        SystemScope.COLLECTION,
        ComplaintCategory.MIS_ISSUED_FEE,
        "Charged an organisation fee I already paid last semester",
        "I paid this at the office on the first week of last semester and "
        "have the receipt. It has been billed again this term. Receipt "
        "number and date are in the attachment.",
        True,
    ),
    (
        SystemScope.COLLECTION,
        ComplaintCategory.PAYMENT_NOT_REFLECTED,
        "GCash payment from last Friday still not showing",
        "I sent the payment on Friday afternoon and the reference number "
        "came back fine, but my balance has not moved since. Nothing shows "
        "under my submissions either.",
        True,
    ),
    (
        SystemScope.COLLECTION,
        ComplaintCategory.WRONG_AMOUNT,
        "Fee shows the full amount after a partial payment",
        "I paid half of this at the counter and the receipt says so, but "
        "the balance on my account is still the whole amount.",
        True,
    ),
    (
        SystemScope.COLLECTION,
        ComplaintCategory.DUPLICATE_CHARGE,
        "The same event fee appears twice on my list",
        "There are two identical rows for the same event, same amount, same "
        "date. I only attended once and only owe one.",
        True,
    ),
    (
        SystemScope.LOCKER,
        ComplaintCategory.SERVICE_ISSUE,
        "Locker was already occupied when I was given the key",
        "The locker assigned to me had somebody else's things in it. I did "
        "not touch them, but I have nowhere to put mine and I have been "
        "billed for the subscription.",
        False,
    ),
    (
        SystemScope.ATTENDANCE,
        ComplaintCategory.RECORD_ERROR,
        "Marked absent for an event I signed in to",
        "I signed in at the entrance and the scanner beeped, but the "
        "attendance record says absent and I have been fined for it.",
        False,
    ),
    (
        SystemScope.CLEARANCE,
        ComplaintCategory.RECORD_ERROR,
        "Clearance item still unsigned after the adviser signed it",
        "My adviser signed the form in person last week. The system still "
        "shows the item as pending and I cannot finish my clearance.",
        False,
    ),
    (
        SystemScope.PLATFORM,
        ComplaintCategory.OTHER,
        "Cannot open the fee list on a phone",
        "The table runs off the side of the screen on my phone and I cannot "
        "scroll it sideways, so I can only see the first column.",
        False,
    ),
]


class Command(SeedCommand):
    seed_name = "complaints"
    help = "Create demo student complaints, some answered and some still open."
    depends_on = ("seed_students", "seed_fees", "seed_users")
    feature = features.COMPLAINTS

    def reset(self, **options):
        deleted, _ = Complaint.objects.all().delete()
        self.warn(f"deleted {deleted} complaint row(s)")

    def _handler_for(self, institute: Institute) -> User | None:
        return (
            User.objects.filter(
                institute=institute,
                groups__name__in=[
                    Roles.CMS_STAFF,
                    Roles.CMS_ADMIN,
                    Roles.SCHOOL_ADMIN,
                ],
            )
            .order_by("id")
            .first()
        )

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")

        if Complaint.objects.exists():
            self.info("complaints already present; nothing to do (use --reset)")
            return

        for institute in institutes:
            self._seed_for(institute)

    def _seed_for(self, institute: Institute) -> None:
        students = list(
            Student.objects.filter(program__institute=institute).order_by("id")
        )
        if not students:
            return

        handler = self._handler_for(institute)
        counts = {"submitted": 0, "acknowledged": 0, "resolved": 0, "rejected": 0}

        for index in range(len(COMPLAINTS) * self.scale):
            system, category, title, description, wants_fee = COMPLAINTS[
                index % len(COMPLAINTS)
            ]
            student = self.pick(students)

            complaint = Complaint.objects.create(
                student=student,
                system=system,
                category=category,
                related_fee=self._fee_for(student) if wants_fee else None,
                title=title,
                description=description,
            )

            roll = self.rng.random()
            if handler is None or roll < 0.35:
                counts["submitted"] += 1
                continue

            if roll < 0.5:
                services.acknowledge_complaint(complaint=complaint, actor=handler)
                counts["acknowledged"] += 1
                continue

            if roll < 0.65:
                services.reject_complaint(
                    complaint=complaint,
                    actor=handler,
                    response=(
                        "We checked the record against the receipts for that "
                        "week and the charge is correct. Please bring the "
                        "receipt to the office if you still disagree - we "
                        "will look at it with you."
                    ),
                )
                counts["rejected"] += 1
                continue

            services.resolve_complaint(
                complaint=complaint,
                actor=handler,
                response=(
                    "You were right - the entry was duplicated when the batch "
                    "was generated. The extra one has been cancelled and your "
                    "balance is corrected. Sorry for the trouble."
                ),
            )
            counts["resolved"] += 1

        self.created("complaint", sum(counts.values()))
        self.info(
            "  {submitted} new, {acknowledged} being looked at, "
            "{resolved} resolved, {rejected} closed".format(**counts)
        )

    def _fee_for(self, student: Student) -> Fee | None:
        return (
            Fee.objects.filter(student=student)
            .order_by("-created_at")
            .first()
        )
