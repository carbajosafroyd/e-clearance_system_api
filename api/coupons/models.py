"""
Coupons: relief the institute hands out, rather than relief a student earns.

Staff issue codes — for turning up to an event, for volunteering, for placing
in a competition — and a student redeems one against a fee. The reward is
concrete and immediate, which is the whole point: a number in a ledger does
not get anyone to a general assembly.

Not points
----------
Coupons live in their own app, behind their own flag, with their own
permissions, and share no wallet, rate or policy with the contribution
scheme. The difference that justifies it:

* points are **earned**, so a student can only ever spend what they have;
* a coupon is **issued**, so it exists before anyone has proved they deserve
  it and can be photographed, forwarded or found on the floor.

Almost everything below is about that second point — binding a code to one
person, and being able to take it back.

Both can apply to one fee. Their sum is capped at what is owed, so a fee can
never go below zero however they stack.

Where the money goes
--------------------
Applying a redemption raises ``Fee.discount_total`` and lowers
``Fee.balance`` by the same amount. ``total_amount`` is never touched: it is
what was billed, and it has to go on saying so. See ``api/fees/models.py``.

Taking it back
--------------
Two different operations, and conflating them is how a student ends up with
a debt they had no way to see coming:

* **void** — the coupon was never spent. Nothing moves.
* **reverse** — it was applied to a fee. The money comes back onto the
  balance, which is safe while the fee is unpaid and dangerous once it is
  not. Reversing against a settled fee needs its own permission, and the
  student is always told.

Revoking a *batch* only voids; it never reverses. The obvious implementation
of "recall this print run" is a loop that reverses everything, and it would
quietly re-bill every student who had already spent one.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.BaseModel import ActorStampedModel, TimeStampedModel
from core.choices import AcademicYear, Semester


ZERO = Decimal("0.00")


class CouponBatch(ActorStampedModel):
    """One issuing run — and the thing you revoke when it goes wrong.

    Modelled on ``GeneratedFeeBatch``, with one deliberate difference: a
    batch is **revoked, never deleted**. A redeemed coupon has touched
    somebody's money and that has to stay on the record.

    Every constraint lives here rather than on the individual coupon. A print
    run is issued under one set of rules; letting two coupons from the same
    run disagree makes the export unreadable and the support conversation
    impossible. The only per-coupon restriction is ``Coupon.student``.
    """

    class Kind(models.TextChoices):
        SINGLE = "single", "Individual coupons"
        MULTI_USE = "multi_use", "One shared code"
        QR_CAMPAIGN = "qr_campaign", "QR campaign"

    class ValueType(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        PERCENT = "percent", "Percentage"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"

    institute = models.ForeignKey(
        "institutes.Institute", on_delete=models.CASCADE, related_name="coupon_batches"
    )
    name = models.CharField(
        max_length=150, help_text="What this run was for, e.g. 'IC Week 2026 raffle'."
    )
    #: Shown to the student on the coupon and in their history. A code with a
    #: story attached is worth more than a code.
    reason = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Why it was given, e.g. 'Quiz bee, 2nd place'.",
    )

    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SINGLE)
    value_type = models.CharField(
        max_length=10, choices=ValueType.choices, default=ValueType.FIXED
    )
    value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Pesos when fixed, percent when percent.",
    )
    #: Ceiling for a percentage coupon. "10% off" with no cap is an open
    #: cheque against whatever the largest fee in the system turns out to be.
    max_discount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    # -- constraints --------------------------------------------------------

    #: An allow-list, never a deny-list. Empty means any category.
    #:
    #: With a deny-list, adding a new collection category would silently
    #: widen every coupon ever issued - somebody creates "Graduation Fee" in
    #: March and a stack of event coupons from October can suddenly be spent
    #: on it. An allow-list fails closed.
    categories = models.ManyToManyField(
        "collection_categories.CollectionCategory",
        blank=True,
        related_name="coupon_batches",
    )
    academic_year = models.CharField(
        max_length=9, choices=AcademicYear.choices(), null=True, blank=True
    )
    semester = models.CharField(
        max_length=10, choices=Semester.choices, null=True, blank=True
    )
    #: Refuse fees owing less than this. Without a floor a 100 coupon spent
    #: on a 20 fee burns 80 of value and the student feels cheated - and
    #: carrying the remainder forward would mean inventing a credit balance
    #: this system does not have.
    min_fee_balance = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    valid_from = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    # -- who may claim, for a QR campaign -----------------------------------

    target_program = models.ForeignKey(
        "programs.Program",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    target_year_level = models.PositiveSmallIntegerField(null=True, blank=True)

    #: One coupon out of this run per student, however many it contains.
    #:
    #: Default on, because the alternative is what a bearer run actually did
    #: without it: "one claim per student" was enforced per *coupon*, so a
    #: hundred printed codes on a table could all be taken by the first
    #: person to reach it. Nothing was over-redeemed - every code was used
    #: once - and the batch was still emptied by one student.
    #:
    #: Off is for the runs where that is the point: a raffle where somebody
    #: may hold several tickets, or a sheet handed to one club officer to
    #: distribute.
    one_per_student = models.BooleanField(default=True)

    total_created = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )

    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_coupon_batches",
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_coupon_batches",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "coupon_batches"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["institute", "status"]),
            models.Index(fields=["-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(value__gt=0), name="coupon_batch_value_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("valid_from")),
                name="coupon_batch_window_ordered",
            ),
        ]
        permissions = [
            # Issuing is not "add a row": it creates instruments that reduce
            # money owed. Same argument as GeneratedFeeBatch.generate_fees.
            ("issue_couponbatch", "Can issue a batch of coupons"),
            ("revoke_couponbatch", "Can revoke a whole batch"),
            ("export_couponbatch", "Can export a batch for printing"),
        ]

    @property
    def is_percentage(self) -> bool:
        return self.value_type == self.ValueType.PERCENT

    def describe_term(self) -> str:
        """'1st Semester 2025-2026' / '2025-2026' / 'Any term'.

        Both halves are optional and independent: a coupon can be scoped to a
        year without a semester, or to "second semester, whichever year".
        Unscoped is the default and says so in words - a blank would read as
        "not configured yet" rather than as a deliberate choice.
        """
        if self.semester and self.academic_year:
            return f"{self.get_semester_display()} {self.academic_year}"
        if self.academic_year:
            return str(self.academic_year)
        if self.semester:
            return f"{self.get_semester_display()}, any year"
        return "Any term"

    def describe_value(self) -> str:
        """'₱50 off' / '10% off, max ₱100' — for exports and the UI."""
        if self.is_percentage:
            capped = f", max ₱{self.max_discount:,.2f}" if self.max_discount else ""
            return f"{self.value:g}% off{capped}"
        return f"₱{self.value:,.2f} off"

    def __str__(self):
        return f"{self.name} ({self.describe_value()})"


class Coupon(ActorStampedModel):
    """One code.

    ``student`` is what makes a leaked code worthless. A bound coupon is
    refused for anybody else, so it can be printed on a list, photographed
    and posted in a group chat and still only be redeemable by the person
    named on it. Leaving it null makes a bearer instrument, which is the
    thing every other control here is working around.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        FULLY_REDEEMED = "fully_redeemed", "Fully redeemed"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    batch = models.ForeignKey(
        CouponBatch, on_delete=models.CASCADE, related_name="coupons"
    )
    code = models.CharField(max_length=32, unique=True, db_index=True)

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="coupons",
        help_text="Bound to one student. Null makes it a bearer coupon.",
    )

    max_redemptions = models.PositiveIntegerField(default=1)
    #: How many of this coupon's uses are **spoken for** - claimed *or*
    #: applied, not merely spent.
    #:
    #: Claiming has to take the use, or a shared code can be handed to more
    #: people than it can pay: two students claim the last use, the first to
    #: apply gets it, and the second is left holding a coupon their account
    #: told them was theirs. The contribution scheme reaches the same
    #: conclusion for the same reason - points leave the wallet when a
    #: discount is *requested*, not when it is approved.
    #:
    #: Given back when a hold is released or a redemption reversed.
    uses_taken = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )

    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_coupons",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "coupons"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["batch", "status"]),
            models.Index(fields=["student", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(max_redemptions__gt=0),
                name="coupon_max_redemptions_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(uses_taken__lte=models.F("max_redemptions")),
                name="coupon_not_over_redeemed",
            ),
        ]
        permissions = [
            ("revoke_coupon", "Can void a single unspent coupon"),
            ("redeem_coupon", "Can redeem a coupon against a fee"),
        ]

    @property
    def remaining(self) -> int:
        """Uses nobody is holding. Zero means the coupon is spoken for."""
        return max(0, self.max_redemptions - self.uses_taken)

    @property
    def is_spent(self) -> bool:
        """Has any of it actually reached a fee?

        Not the same as `uses_taken > 0`: a coupon somebody is merely holding
        has taken a use but moved no money, so it can still be voided. Only a
        redemption that reached a fee needs reversing instead.

        Reads the prefetch cache when there is one. Both this and
        `status_label` ask the question, and every serialized coupon carries
        both - so on a page of ten codes the naive `.exists()` was twenty
        queries for rows already in memory.
        """
        cached = getattr(self, "_prefetched_objects_cache", {}).get("redemptions")
        if cached is not None:
            return any(
                row.status == CouponRedemption.Status.APPLIED for row in cached
            )
        return self.redemptions.filter(
            status=CouponRedemption.Status.APPLIED
        ).exists()

    @property
    def status_label(self) -> str:
        """What to print, which is not always what the column says.

        Claiming takes a use, so a coupon a student is holding and has not
        spent lands on FULLY_REDEEMED - accurate about its capacity, a lie
        about the money. "Claimed" is the true word for that state, and it is
        the one that tells a coordinator a recall will still catch it.
        """
        if self.status == self.Status.FULLY_REDEEMED and not self.is_spent:
            return "Claimed"
        return self.get_status_display()

    def __str__(self):
        return self.code


class CouponRedemption(ActorStampedModel):
    """A coupon spent, and the record that lets it be undone.

    Two stages on purpose. **Claimed** is "this is mine now" — a scan at an
    event, with no fee named. **Applied** is "put it against this one". A
    student handed a coupon on a Tuesday for a fine they get in November
    needs the gap between them.

    ``previous_*`` snapshots the fee before the write. The reversal works off
    ``amount`` — the only figure that survives a partial cap — but the
    snapshot is what makes "what did this do?" answerable later without
    replaying anything.
    """

    class Status(models.TextChoices):
        CLAIMED = "claimed", "Claimed, not yet applied"
        APPLIED = "applied", "Applied to a fee"
        REVERSED = "reversed", "Reversed"

    coupon = models.ForeignKey(
        Coupon, on_delete=models.CASCADE, related_name="redemptions"
    )
    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="coupon_redemptions"
    )
    fee = models.ForeignKey(
        "fees.Fee",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="coupon_redemptions",
    )

    amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    previous_discount_total = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    previous_balance = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CLAIMED, db_index=True
    )
    applied_at = models.DateTimeField(null=True, blank=True)

    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reversed_coupon_redemptions",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversal_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "coupon_redemptions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["fee", "status"]),
            models.Index(fields=["coupon", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="coupon_redemption_amount_not_negative",
            ),
        ]
        permissions = [
            ("reverse_couponredemption", "Can reverse a spent coupon"),
            (
                "reverse_paid_couponredemption",
                "Can reverse a coupon on a fee that is already paid",
            ),
        ]

    def __str__(self):
        target = f"fee #{self.fee_id}" if self.fee_id else "unapplied"
        return f"{self.coupon_id} -> {target} ({self.status})"


class CouponClaim(TimeStampedModel):
    """One student's claim, and the two limits that need enforcing.

    A QR poster is a single coupon that many people scan, so "one claim per
    student" needs somewhere to live. The unique constraint is the
    enforcement — not a count, not a check-then-write, both of which two
    phones scanning at once will walk straight through.

    ``batch_scope`` is the second limit, one step up: at most one coupon out
    of a whole run. It holds the batch when that run says
    ``one_per_student``, and NULL when it does not — and because NULLs do not
    collide, one constraint covers both settings without a second table or a
    conditional index. A count check here would have the same race the
    per-coupon rule already avoids, only wider: two different codes from the
    same batch, scanned at the same moment.
    """

    coupon = models.ForeignKey(
        Coupon, on_delete=models.CASCADE, related_name="claims"
    )
    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="coupon_claims"
    )
    #: The batch, when it allows one coupon per student. NULL otherwise.
    batch_scope = models.ForeignKey(
        CouponBatch,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scoped_claims",
    )

    class Meta:
        db_table = "coupon_claims"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["coupon", "student"], name="one_claim_per_student_per_coupon"
            ),
            models.UniqueConstraint(
                fields=["batch_scope", "student"],
                name="one_claim_per_student_per_batch",
            ),
        ]

    def __str__(self):
        return f"{self.student_id} claimed {self.coupon_id}"
