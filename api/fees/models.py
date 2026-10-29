from decimal import Decimal

from django.db import models
from api.institutes.models import Institute
from api.collection_categories.models import CollectionCategory
from core.BaseModel import ActorStampedModel
from config import settings
from core.choices import Semester, AcademicYear, get_default_academic_year
from api.institutes_attendance_events.models import InstituteAttendanceEvent


class GeneratedFeeBatch(ActorStampedModel):
    """One run of a fee generation action.

    Every Fee it created points back here via `fee_records`, which is what
    makes a bad run reversible: delete the batch and the fees go with it.
    """

    category = models.ForeignKey(
        CollectionCategory, models.CASCADE, related_name="generated_fee_batch"
    )
    institute = models.ForeignKey(
        Institute, models.CASCADE, related_name="generated_fee_batch"
    )
    academic_year = models.CharField(
        max_length=9,
        choices=AcademicYear.choices, # type: ignore
        default=get_default_academic_year,
    )
    semester = models.CharField(
        max_length=20, choices=Semester.choices, default=Semester.FIRST
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_fee_batch",
    )
    total_created_fees = models.IntegerField(default=0)

    class Meta: # type: ignore
        db_table = "generated_fee_batch"
        # Generation is not "add a batch row": it bills every eligible
        # student in an institute. It gets its own permission so the desk
        # staff who may read batches are not automatically able to run one.
        permissions = [
            ("generate_fees", "Can run collection fee generation"),
            ("generate_attendance_fees", "Can run attendance fee generation"),
        ]
        # Deliberately not unique on (institute, semester, academic_year,
        # category): re-running generation after adding students is a normal
        # operation, and each run is its own auditable record.
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Batch {self.institute} {self.semester}/{self.academic_year} ({self.id})" # type: ignore
        )


class Fee(ActorStampedModel):
    """A single charge against a student.

    Four numbers, and the split between them is what makes a fee auditable:

        total_amount    what was billed. Fixed at issue.
        discount_total  relief granted against it - points, coupons, anything
                        later. Only a reduction service writes this.
        balance         still outstanding. What payments draw down.
        collected       not stored: total_amount - discount_total - balance.

    A partially paid fee stays PENDING with a reduced balance - there is no
    PARTIAL status.

    Discounts used to be taken off `total_amount` directly, which left the
    row unable to say what it had originally charged: a 500 fee with 50
    forgiven claimed it was always 450, and the only record of the other 50
    was a row in another app that a deployment may have switched off. Keeping
    the relief in its own column costs one field and answers "billed,
    forgiven, collected, owed" without replaying a ledger.

    Both halves still move together on a discount - `discount_total` up and
    `balance` down by the same amount - because `collected` is derived. Move
    the balance alone and every discount books itself as money received; move
    it without recording the relief and the collection report's
    reconciliation reads the drawdown as revenue given away unrecorded.
    """

    class FeeStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        # No PARTIAL: a part-paid fee is PENDING with balance < total_amount,
        # which keeps the status filters single-valued and indexable.
        PAID = "paid", "Paid"
        WAIVED = "waived", "Waived"
        OVERDUE = "overdue", "Overdue"

    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="fees"
    )
    category = models.ForeignKey(
        "collection_categories.CollectionCategory",
        on_delete=models.CASCADE,
        related_name="fees",
    )
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    #: Relief granted against this fee. Written only by a reduction service
    #: (see api/contributions/services.py); zero on every fee where no
    #: reduction scheme is running, which is the common case.
    discount_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    balance = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20, choices=FeeStatus.choices, default=FeeStatus.PENDING
    )
    due_date = models.DateTimeField(null=True)
    remarks = models.TextField(null=True, blank=True)

    academic_year = models.CharField(
        max_length=9,
        choices=AcademicYear.choices, # type: ignore
        default=get_default_academic_year,
    )
    semester = models.CharField(
        max_length=10, choices=Semester.choices, default=Semester.FIRST
    )

    gen_fee_batch = models.ForeignKey(
        GeneratedFeeBatch,
        on_delete=models.CASCADE,
        related_name="fee_records",
        null=True,
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    institute_attendance_event = models.ForeignKey(
        InstituteAttendanceEvent,
        on_delete=models.SET_NULL,
        null=True,
        related_name="fees",
    )

    class Meta: # type: ignore
        db_table = "fees"
        # Forgiving a debt, un-forgiving it and the overdue sweep are each a
        # distinct authority - all three would collapse into `change_fee`
        # without these.
        permissions = [
            ("waive_fee", "Can waive the remaining balance of a fee"),
            ("unwaive_fee", "Can restore a waived fee to pending"),
            ("mark_fee_overdue", "Can run the past-due sweep"),
            ("view_fee_discount_history", "Can view a fee's discount history"),
        ]
        indexes = [
            models.Index(fields=["student", "academic_year", "semester"]),
            models.Index(fields=["status", "academic_year", "semester"]),
            models.Index(fields=["category", "status"]),
            models.Index(fields=["issued_by", "-updated_at"]),
            models.Index(fields=["due_date"]),
        ]
        ordering = ["-updated_at"]
        constraints = [
            # Over-deduction is prevented in the payment service, but these
            # make it physically impossible even if a code path slips through:
            # a balance can never go negative and can never exceed what is
            # still expected (payment cancellation restores balance - the
            # ceiling catches a double-restore).
            models.CheckConstraint(
                condition=models.Q(balance__gte=0),
                name="fee_balance_not_negative",
            ),
            # Net of relief, not gross: a fully discounted fee has a balance
            # of zero and a ceiling of zero, and the old `balance <=
            # total_amount` would have let a discounted fee be over-paid back
            # up to its original price.
            models.CheckConstraint(
                condition=models.Q(
                    balance__lte=models.F("total_amount") - models.F("discount_total")
                ),
                name="fee_balance_within_net",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_total__gte=0),
                name="fee_discount_total_not_negative",
            ),
            # You cannot forgive more than you charged.
            models.CheckConstraint(
                condition=models.Q(discount_total__lte=models.F("total_amount")),
                name="fee_discount_within_total",
            ),
        ]
        # No DB-level uniqueness here, and it is not an oversight:
        #
        #   * (student, category, academic_year, semester) is the right key
        #     for flat collection fees, but wrong for Attendance - one student
        #     legitimately holds several Attendance fees per term, one per
        #     event. The constraint would need `WHERE
        #     institute_attendance_event IS NULL`, and MySQL has no partial
        #     indexes.
        #   * (student, institute_attendance_event) would only bind attendance
        #     rows, since MySQL treats NULLs in a unique index as distinct.
        #
        # Double-billing is instead prevented in
        # `services/generation/engine.py`, which takes a row lock on the scope
        # (the category or the event) and re-checks for existing fees inside
        # that lock before inserting.

    def __str__(self):
        return (
            f"Fee #{self.pk} | "
            f"{self.student} | year: {self.student.s_lvl} | "
            f"{self.category.category_name} | "
            f"{self.academic_year} {self.semester} Sem| "
            f"{self.get_status_display()} | " # type: ignore
            f"Balance: {self.balance}/{self.total_amount}"
        )
