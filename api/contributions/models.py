"""
Student contributions and the points they earn.

The loop
--------
1. A student files a **Contribution** - a bug report, a recommendation, a
   feature idea - against exactly one system (`SystemScope`).
2. An admin for that system reviews it. Approving awards points.
3. Points land in a **PointAccount**, one per student *per system*, and every
   movement is a **PointTransaction** so a balance is always explainable.
4. The student spends points on a **FeeDiscount**: an admin-approved
   reduction of a fee. Enough points and the fee reaches zero, which is the
   "waived by contribution" case.

Why per system
--------------
Each system accepts its own reports and pays for them out of its own budget.
A locker bug report should not fund a discount the collection office has to
absorb, and a system with no reviewers should not silently accumulate an
unpaid liability. So accounts, policies, ledger entries and discounts all
carry a `system`, and the leaderboard can be read per system or overall.

Money
-----
An approved discount raises the fee's `discount_total` and lowers its
`balance` by the same amount. That keeps the reporting identity honest:
`collected` is derived as `total_amount - discount_total - balance`, so
writing the balance down on its own would have booked the discount as money
received. Moving both leaves `collected` untouched and lowers what is still
expected, which is what a discount actually is - while `total_amount` goes on
saying what was billed, so the fee can still be asked what it originally
charged.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from core.BaseModel import ActorStampedModel, TimeStampedModel
from core.choices import SystemScope


class ContributionKind(models.TextChoices):
    """What a student is filing. Each kind has its own default award."""

    BUG_REPORT = "bug_report", "Bug report"
    RECOMMENDATION = "recommendation", "Recommendation"
    FEATURE_REQUEST = "feature_request", "Feature request"
    IMPROVEMENT = "improvement", "Usability improvement"
    DATA_CORRECTION = "data_correction", "Data correction"
    OTHER = "other", "Other"


class ContributionImpact(models.TextChoices):
    """How much the report is worth, as a multiplier on the kind's award."""

    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


#: Multiplier applied to the kind's base award. Kept next to the enum rather
#: than in the policy JSON: the *scale* is a fixed idea, only the base award
#: is configurable.
IMPACT_MULTIPLIERS = {
    ContributionImpact.LOW: Decimal("0.5"),
    ContributionImpact.MEDIUM: Decimal("1.0"),
    ContributionImpact.HIGH: Decimal("2.0"),
    ContributionImpact.CRITICAL: Decimal("3.0"),
}

#: Fallback base award per kind, used when an institute has no policy row.
DEFAULT_AWARDS = {
    ContributionKind.BUG_REPORT: 30,
    ContributionKind.RECOMMENDATION: 20,
    ContributionKind.FEATURE_REQUEST: 25,
    ContributionKind.IMPROVEMENT: 15,
    ContributionKind.DATA_CORRECTION: 10,
    ContributionKind.OTHER: 5,
}


def default_awards() -> dict:
    return {str(kind): points for kind, points in DEFAULT_AWARDS.items()}


class PointPolicy(TimeStampedModel):
    """How one system, in one institute, values and pays out contributions.

    Everything a coordinator would want to tune without a deploy lives here:
    what each kind of report is worth, what a point is worth in pesos, and
    how much of a fee a student is allowed to discount away.
    """

    institute = models.ForeignKey(
        "institutes.Institute",
        on_delete=models.CASCADE,
        related_name="point_policies",
    )
    system = models.CharField(max_length=20, choices=SystemScope.choices)

    #: Base points per contribution kind: ``{"bug_report": 30, ...}``.
    awards = models.JSONField(default=default_awards)

    #: What one point is worth when redeemed.
    peso_per_point = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    #: Redemption bounds. The student chooses how many points to spend
    #: between these, which is the "let the user decide" part of the scheme.
    min_points_per_redemption = models.PositiveIntegerField(default=10)
    max_points_per_redemption = models.PositiveIntegerField(default=500)

    #: Ceiling on how much of a single fee points may cover. 100 means a fee
    #: can be discounted to zero - the "waived by contribution" case.
    max_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("100.00")
    )

    #: Turning this off stops new awards and new redemptions for this system
    #: without deleting anyone's balance.
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "contribution_point_policies"
        ordering = ["institute_id", "system"]
        constraints = [
            models.UniqueConstraint(
                fields=["institute", "system"],
                name="unique_point_policy_per_institute_system",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    max_points_per_redemption__gte=models.F(
                        "min_points_per_redemption"
                    )
                ),
                name="point_policy_redemption_bounds_ordered",
            ),
        ]
        verbose_name_plural = "point policies"

    def __str__(self):
        return f"{self.institute} / {self.get_system_display()} point policy"

    def award_for(self, kind: str, impact: str) -> int:
        """Base award for `kind`, scaled by `impact`, floored at 0."""
        base = self.awards.get(str(kind), DEFAULT_AWARDS.get(kind, 0))
        multiplier = IMPACT_MULTIPLIERS.get(impact, Decimal("1.0"))
        return max(0, int(Decimal(base) * multiplier))

    def peso_value(self, points: int) -> Decimal:
        return (Decimal(points) * self.peso_per_point).quantize(Decimal("0.01"))


class Contribution(ActorStampedModel):
    """One report or recommendation, filed by a student against one system."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        UNDER_REVIEW = "under_review", "Under review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        # Real, but somebody already reported it. Terminal and unpaid, kept
        # separate from REJECTED so "we were wrong" and "we already knew"
        # do not look the same in the student's history.
        DUPLICATE = "duplicate", "Duplicate"

    #: Statuses a reviewer can no longer act on.
    TERMINAL_STATUSES = ("approved", "rejected", "duplicate")

    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="contributions"
    )
    system = models.CharField(
        max_length=20,
        choices=SystemScope.choices,
        db_index=True,
        help_text="Which system this is about. Points are earned in that system.",
    )
    kind = models.CharField(
        max_length=30,
        choices=ContributionKind.choices,
        default=ContributionKind.RECOMMENDATION,
    )
    impact = models.CharField(
        max_length=10,
        choices=ContributionImpact.choices,
        default=ContributionImpact.MEDIUM,
        help_text="Set by the student, confirmed or corrected by the reviewer.",
    )

    title = models.CharField(max_length=150)
    description = models.TextField()
    #: Uploaded evidence, same shape as PaymentSubmission.screenshot.
    attachments = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    points_awarded = models.PositiveIntegerField(default=0)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_contributions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_remarks = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "contributions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["student", "-created_at"]),
            models.Index(fields=["system", "status"]),
            models.Index(fields=["status", "-created_at"]),
        ]
        permissions = [
            ("review_contribution", "Can approve or reject a student contribution"),
            ("view_all_contributions", "Can view contributions across all students"),
            ("view_contribution_leaderboard", "Can view the contribution ranking"),
        ]

    def __str__(self):
        return f"[{self.system}] {self.title} ({self.status})"

    @property
    def institute_id(self):
        """Institute the contribution belongs to, via the student's program."""
        return self.student.program.institute_id


class PointAccount(TimeStampedModel):
    """A student's wallet for one system.

    `balance` is stored rather than derived so the leaderboard and the
    redemption check are one read instead of an aggregate over the ledger.
    It is only ever moved by `services.apply_transaction`, which writes the
    matching PointTransaction in the same locked block - so the invariant
    `balance == total_earned - total_spent` holds, and the ledger explains it.
    """

    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="point_accounts"
    )
    system = models.CharField(max_length=20, choices=SystemScope.choices)

    total_earned = models.PositiveIntegerField(default=0)
    total_spent = models.PositiveIntegerField(default=0)
    balance = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "contribution_point_accounts"
        ordering = ["-balance", "student_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "system"],
                name="unique_point_account_per_student_system",
            ),
        ]
        indexes = [
            models.Index(fields=["system", "-total_earned"]),
        ]

    def __str__(self):
        return f"{self.student} / {self.get_system_display()}: {self.balance} pts"


class PointTransaction(TimeStampedModel):
    """One movement of points. Append-only: corrections are new rows."""

    class Reason(models.TextChoices):
        CONTRIBUTION_APPROVED = "contribution_approved", "Contribution approved"
        CONTRIBUTION_REVOKED = "contribution_revoked", "Contribution approval revoked"
        REDEMPTION = "redemption", "Spent on a fee discount"
        REDEMPTION_REVERSED = "redemption_reversed", "Fee discount cancelled"
        #: Not a cancellation: the discount went through, for less than was
        #: reserved, because the fee owed less by the time it was approved.
        #: The student's ledger is read by the student, and "cancelled" would
        #: be the wrong word on a discount they did receive.
        REDEMPTION_ADJUSTED = (
            "redemption_adjusted",
            "Fee discount capped, unused points returned",
        )
        ADJUSTMENT = "adjustment", "Manual adjustment"

    account = models.ForeignKey(
        PointAccount, on_delete=models.CASCADE, related_name="transactions"
    )
    #: Signed: positive earns, negative spends.
    delta = models.IntegerField()
    balance_after = models.PositiveIntegerField()

    reason = models.CharField(max_length=30, choices=Reason.choices)
    remarks = models.TextField(null=True, blank=True)

    contribution = models.ForeignKey(
        Contribution,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="point_transactions",
    )
    fee_discount = models.ForeignKey(
        "contributions.FeeDiscount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="point_transactions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "contribution_point_transactions"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["account", "-created_at"]),
            models.Index(fields=["reason"]),
        ]

    def __str__(self):
        sign = "+" if self.delta >= 0 else ""
        return f"{sign}{self.delta} pts ({self.reason}) -> {self.balance_after}"


class FeeDiscount(ActorStampedModel):
    """Points spent to reduce one fee.

    The student picks how many points to put in (within the policy's bounds);
    an admin approves. On approval the fee's `discount_total` rises and its
    `balance` drops by `amount` - see the module docstring for why both move.

    `previous_total_amount` / `previous_balance` are the undo record: a
    cancelled discount restores exactly what it took, and refunds the points.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    fee = models.ForeignKey(
        "fees.Fee", on_delete=models.CASCADE, related_name="discounts"
    )
    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="fee_discounts"
    )
    #: Which wallet the points come out of.
    system = models.CharField(max_length=20, choices=SystemScope.choices)

    points_spent = models.PositiveIntegerField()
    #: Peso value of those points at the policy's rate when it was requested.
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    previous_total_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    previous_balance = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_fee_discounts",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_fee_discounts",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_fee_discounts",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "contribution_fee_discounts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["fee", "-created_at"]),
            models.Index(fields=["student", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(points_spent__gt=0),
                name="fee_discount_points_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="fee_discount_amount_positive",
            ),
        ]
        permissions = [
            ("review_feediscount", "Can approve or reject a points-based discount"),
            ("cancel_feediscount", "Can cancel an approved discount"),
        ]

    def __str__(self):
        return (
            f"Discount #{self.pk}: {self.points_spent} pts "
            f"({self.amount}) off fee #{self.fee_id} [{self.status}]"
        )
