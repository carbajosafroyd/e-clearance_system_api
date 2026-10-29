"""
Student complaints, and the staff answer that closes one.

Why this is not a contribution
------------------------------
A contribution is an offer - a student noticed something and chose to tell
us, and the institute may pay for it. A complaint is a claim: "you charged me
for a locker I never had". The institute owes an answer whether or not it
runs a rewards scheme, so complaints live in their own app, behind their own
feature flag, and never touch a point balance. An institute that switches the
whole contributions module off still has this.

The shape of one
----------------
* filed against exactly one **system** (`SystemScope`), which decides who
  reads it - the same rule contributions use;
* optionally pointed at the **fee** it is about, because "this fee is wrong"
  is the case this exists for, and a complaint that names the row is one a
  clerk can act on without a conversation;
* answered once. `respond`, `reject` and `withdraw` are terminal, and every
  one of them requires words: a closed complaint that says nothing is how a
  student learns not to file the next one.

Status flow
-----------
    SUBMITTED ──acknowledge──▶ ACKNOWLEDGED ──┬──resolve──▶ RESOLVED
        │                            │        └──reject───▶ REJECTED
        ├──resolve/reject────────────┘
        └──withdraw──▶ WITHDRAWN   (the student's own doing, while open)

Acknowledging is optional and exists only so a student can see somebody has
picked it up; the resolve/reject paths do not require it.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.BaseModel import ActorStampedModel
from core.choices import SystemScope


class ComplaintCategory(models.TextChoices):
    """What the student says went wrong.

    Written from the student's side on purpose - these labels appear in the
    form they fill in, and "Fee I should not have been charged" is a sentence
    somebody can pick out of a list under stress. Billing cases come first
    because they are the bulk of them.
    """

    MIS_ISSUED_FEE = "mis_issued_fee", "Fee I should not have been charged"
    WRONG_AMOUNT = "wrong_amount", "Wrong amount on a fee"
    PAYMENT_NOT_REFLECTED = "payment_not_reflected", "Payment not reflected"
    DUPLICATE_CHARGE = "duplicate_charge", "Charged twice for the same thing"
    RECORD_ERROR = "record_error", "My record is wrong"
    SERVICE_ISSUE = "service_issue", "Problem with a service or facility"
    STAFF_CONDUCT = "staff_conduct", "How I was treated"
    OTHER = "other", "Something else"


#: Categories where naming the fee is what makes the complaint actionable.
#: Advisory - the form nudges, `services` does not refuse, because a student
#: who cannot find the row still deserves to be heard.
FEE_RELATED_CATEGORIES = (
    ComplaintCategory.MIS_ISSUED_FEE,
    ComplaintCategory.WRONG_AMOUNT,
    ComplaintCategory.PAYMENT_NOT_REFLECTED,
    ComplaintCategory.DUPLICATE_CHARGE,
)


class Complaint(ActorStampedModel):
    """One thing a student says the institute got wrong."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        ACKNOWLEDGED = "acknowledged", "Being looked at"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Closed without action"
        WITHDRAWN = "withdrawn", "Withdrawn by the student"

    #: Statuses nobody acts on again.
    TERMINAL_STATUSES = ("resolved", "rejected", "withdrawn")

    #: Statuses the student may still edit or withdraw from.
    OPEN_STATUSES = ("submitted", "acknowledged")

    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="complaints"
    )
    system = models.CharField(
        max_length=20,
        choices=SystemScope.choices,
        db_index=True,
        help_text="Which system this is about. It decides who reads it.",
    )
    category = models.CharField(
        max_length=30,
        choices=ComplaintCategory.choices,
        default=ComplaintCategory.OTHER,
    )

    #: The fee the complaint is about, where there is one. SET_NULL rather
    #: than CASCADE: cancelling the wrong fee is a perfectly good outcome,
    #: and it must not delete the record of the complaint that caused it.
    related_fee = models.ForeignKey(
        "fees.Fee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints",
    )

    title = models.CharField(max_length=150)
    description = models.TextField()
    #: Receipts, screenshots - same shape as PaymentSubmission.screenshot.
    attachments = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SUBMITTED,
        db_index=True,
    )

    #: The staff answer. Required to resolve and to reject, and shown to the
    #: student in the notification either way.
    response = models.TextField(null=True, blank=True)

    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_complaints",
    )
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "complaints"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["student", "-created_at"]),
            models.Index(fields=["system", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]
        permissions = [
            ("resolve_complaint", "Can answer and close a student complaint"),
            ("view_all_complaints", "Can view complaints across all students"),
            ("withdraw_complaint", "Can withdraw one's own complaint"),
        ]

    def __str__(self):
        return f"[{self.system}] {self.title} ({self.status})"

    @property
    def institute_id(self):
        """Institute this belongs to, via the student's program."""
        return self.student.program.institute_id

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES
