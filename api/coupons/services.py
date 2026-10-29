"""
What a coupon actually does, kept out of the views.

The views own HTTP. Everything here is about the two hard parts: refusing a
redemption for the right reason, and being able to take one back afterwards.

Every refusal raises `CouponError` with a `code`, because "this coupon cannot
be used" sends a student to the desk and "this coupon only applies to
Attendance Fines" sends them to the right fee.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from api.fees.models import Fee

from .models import Coupon, CouponBatch, CouponClaim, CouponRedemption
from .utils import generate_code, normalise_code

logger = logging.getLogger(__name__)
User = get_user_model()

ZERO = Decimal("0.00")


class CouponError(Exception):
    """A refusal we can explain, with a code the UI can branch on."""

    def __init__(self, message: str, *, code: str = "invalid"):
        super().__init__(message)
        self.message = message
        self.code = code


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Issuing
# --------------------------------------------------------------------------


#: How many times a whole print run is re-minted after losing a race to
#: another one. Three is generous: a collision needs two runs to draw the
#: same 12 characters out of 32^12 in the same instant.
MINT_ATTEMPTS = 3


def _mint_codes(prefix: str, count: int) -> list[str]:
    """`count` codes nothing else is using.

    Three layers, because each catches what the one before cannot:

    1. A `set` covers the run being built. `bulk_create` would otherwise hand
       the collision to the unique index halfway through, and half a print
       run is worse than none.
    2. One `IN` query covers everything already issued. Per-code `exists()`
       was a query per coupon - five hundred round trips to mint a batch of
       five hundred, on a remote database.
    3. The unique index covers the gap the query cannot: two runs minting at
       once both check before either writes. `issue_batch` retries there.

    Over-generates so the collision case does not need a second round trip.
    """
    if count <= 0:
        return []

    wanted = count
    pool: list[str] = []
    for _ in range(20):
        candidates = {generate_code(prefix) for _ in range(wanted - len(pool))}
        candidates.difference_update(pool)
        taken = set(
            Coupon.objects.filter(code__in=candidates).values_list("code", flat=True)
        )
        pool.extend(candidates - taken)
        if len(pool) >= count:
            return pool[:count]

    raise CouponError(
        "Could not generate unique codes. Please try again.",
        code="code_generation_failed",
    )


@transaction.atomic
def issue_batch(
    *,
    batch: CouponBatch,
    students=None,
    quantity: int = 1,
    max_redemptions: int = 1,
    code_prefix: str = "",
) -> list[Coupon]:
    """
    Mint the coupons for a saved batch.

    Pass `students` for bound coupons — one each, and a leaked code is then
    worth nothing to anybody else. Pass `quantity` for bearer coupons, which
    is the mode every other control in this module exists to compensate for.
    """
    holders = list(students) if students else [None] * max(1, quantity)

    for attempt in range(MINT_ATTEMPTS):
        codes = _mint_codes(code_prefix, len(holders))
        coupons = [
            Coupon(
                batch=batch,
                code=code,
                student=student,
                max_redemptions=max_redemptions,
            )
            for code, student in zip(codes, holders)
        ]
        try:
            # Its own savepoint. An IntegrityError marks the surrounding
            # atomic block unusable, so without this the retry would fail on
            # the next query rather than on the collision.
            with transaction.atomic():
                created = Coupon.objects.bulk_create(coupons)
        except IntegrityError:
            # Another run minted one of these between our check and our
            # write. Nothing is salvageable - bulk_create is all or nothing -
            # so draw a fresh set.
            logger.warning(
                "coupon.mint_collision batch=%s attempt=%s", batch.pk, attempt + 1
            )
            continue

        batch.total_created = len(created)
        batch.save(update_fields=["total_created", "updated_at"])
        return created

    raise CouponError(
        "Could not generate unique codes. Please try again.",
        code="code_generation_failed",
    )


# --------------------------------------------------------------------------
# Looking one up
# --------------------------------------------------------------------------


def find_coupon(raw_code: str) -> Coupon:
    code = normalise_code(raw_code)
    if not code:
        raise CouponError("Enter a coupon code.", code="missing_code")

    coupon = (
        Coupon.objects.select_related("batch", "student")
        .filter(code=code)
        .first()
    )
    if coupon is None:
        raise CouponError("No coupon with that code.", code="not_found")
    return coupon


def check_usable(coupon: Coupon, student, *, now=None) -> None:
    """
    Everything that does not depend on a particular fee.

    Runs at claim time so a student finds out early, and again at apply time
    because weeks pass in between and a batch can be revoked in the gap.
    """
    now = now or timezone.now()
    batch = coupon.batch

    if batch.status == CouponBatch.Status.REVOKED:
        raise CouponError("This coupon is no longer valid.", code="revoked")

    if coupon.status == Coupon.Status.REVOKED:
        raise CouponError("This coupon is no longer valid.", code="revoked")

    # "No uses left" must not refuse the person already holding one. Their
    # claim is what took the use in the first place, and reserving capacity
    # for somebody and then refusing them with it is the exact failure this
    # reservation exists to prevent, pointed the other way.
    if coupon.remaining <= 0 and not holds_a_claim(coupon, student):
        raise CouponError("This coupon has already been used.", code="fully_redeemed")

    if now < batch.valid_from:
        raise CouponError(
            f"This coupon can be used from "
            f"{timezone.localtime(batch.valid_from):%d %b %Y}.",
            code="not_yet_valid",
        )

    if now >= batch.expires_at:
        raise CouponError(
            f"This coupon expired on "
            f"{timezone.localtime(batch.expires_at):%d %b %Y}.",
            code="expired",
        )

    if coupon.student_id and coupon.student_id != student.pk:
        raise CouponError(
            "This coupon was issued to another student.", code="wrong_student"
        )

    # Institute scope. Resolved through the student's program, which is where
    # a student's institute actually lives.
    student_institute = getattr(getattr(student, "program", None), "institute_id", None)
    if student_institute and student_institute != batch.institute_id:
        raise CouponError(
            "This coupon was issued by another institute.", code="wrong_institute"
        )

    if not _may_claim(batch, student):
        raise CouponError(
            "This coupon is for a different group of students.", code="not_eligible"
        )


def holds_a_claim(coupon: Coupon, student) -> bool:
    """Is this student already holding an unspent claim on this coupon?"""
    return CouponRedemption.objects.filter(
        coupon=coupon, student=student, status=CouponRedemption.Status.CLAIMED
    ).exists()


def _may_claim(batch: CouponBatch, student) -> bool:
    if batch.target_program_id and batch.target_program_id != student.program_id:
        return False
    if batch.target_year_level and batch.target_year_level != student.s_lvl:
        return False
    return True


def check_fee(coupon: Coupon, fee: Fee, student) -> None:
    """The constraints that need a fee in hand."""
    batch = coupon.batch

    if fee.student_id != student.pk:
        raise CouponError("That fee belongs to another student.", code="wrong_student")

    if fee.status not in (Fee.FeeStatus.PENDING, Fee.FeeStatus.OVERDUE):
        raise CouponError(
            "That fee is already settled — nothing to discount.", code="fee_settled"
        )

    if fee.balance <= ZERO:
        raise CouponError(
            "That fee has nothing left owing.", code="fee_settled"
        )

    # Allow-list. Empty means any category.
    allowed = list(batch.categories.all())
    if allowed and fee.category_id not in {c.pk for c in allowed}:
        names = ", ".join(c.category_name for c in allowed)
        raise CouponError(
            f"This coupon only applies to {names}.", code="category_not_allowed"
        )

    if batch.academic_year and fee.academic_year != batch.academic_year:
        raise CouponError(
            f"This coupon is for {batch.academic_year} fees.", code="wrong_term"
        )

    if batch.semester and fee.semester != batch.semester:
        raise CouponError(
            f"This coupon is for {batch.get_semester_display()} fees.",
            code="wrong_term",
        )

    if batch.min_fee_balance and fee.balance < batch.min_fee_balance:
        raise CouponError(
            f"This coupon needs a fee of at least ₱{batch.min_fee_balance:,.2f} owing.",
            code="below_minimum",
        )

    # One coupon per fee. Points may stack on top; a second coupon may not.
    # Deliberately the tighter rule - relaxing a validation later is easy,
    # and un-ringing a fee somebody cleared with four coupons is not.
    already = CouponRedemption.objects.filter(
        fee=fee, status=CouponRedemption.Status.APPLIED
    ).exists()
    if already:
        raise CouponError(
            "A coupon has already been applied to that fee.", code="fee_has_coupon"
        )


def quote(coupon: Coupon, fee: Fee) -> Decimal:
    """
    What would come off, without committing anything.

    Percentage is of the **remaining balance**, not of the original bill. A
    student who already paid half a ₱500 fine and then uses "50% off" gets
    ₱125 off what they still owe, not ₱250 off a debt of ₱250 - which would
    clear it entirely and give the coupon a value nobody intended.
    """
    batch = coupon.batch

    if batch.is_percentage:
        amount = fee.balance * batch.value / Decimal("100")
        if batch.max_discount:
            amount = min(amount, batch.max_discount)
    else:
        amount = batch.value

    return money(min(amount, fee.balance))


# --------------------------------------------------------------------------
# Claiming
# --------------------------------------------------------------------------


@transaction.atomic
def claim(*, raw_code: str, student, actor=None) -> CouponRedemption:
    """
    Take a coupon without spending it yet.

    For a shared code the `CouponClaim` row is the enforcement of "once per
    student" - a unique constraint rather than a count, because two phones
    scanning the same poster at the same moment walk straight through a
    check-then-write.
    """
    found = find_coupon(raw_code)
    # Locked before the capacity check: two students scanning the same poster
    # at the same moment would otherwise both read the last use as free.
    coupon = (
        Coupon.objects.select_for_update().select_related("batch").get(pk=found.pk)
    )
    check_usable(coupon, student)

    existing = CouponRedemption.objects.filter(
        coupon=coupon,
        student=student,
        status__in=[
            CouponRedemption.Status.CLAIMED,
            CouponRedemption.Status.APPLIED,
        ],
    ).first()
    if existing:
        if existing.status == CouponRedemption.Status.CLAIMED:
            return existing
        raise CouponError("You have already used this coupon.", code="already_used")

    # Always, not only for shared codes: the per-batch limit needs a row even
    # on a bound one-use coupon, or issuing two to the same student in one run
    # would slip past it.
    _record_claim(coupon, student)

    _take_use(coupon)

    return CouponRedemption.objects.create(
        coupon=coupon,
        student=student,
        status=CouponRedemption.Status.CLAIMED,
        updated_by=actor,
    )


def _claim_scope(batch: CouponBatch):
    """The batch, when it allows one coupon per student. NULL otherwise.

    Written into `CouponClaim.batch_scope`, where a unique constraint turns
    the rule into something two simultaneous scans cannot get past.
    """
    return batch if batch.one_per_student else None


def _record_claim(coupon: Coupon, student) -> None:
    """Take the student's claim on this coupon, and on its run.

    Both limits are unique constraints, so this is where they are enforced -
    the message is chosen by reading which one the database refused, because
    "you already have one from this run" and "you already claimed this code"
    send somebody to different places.
    """
    try:
        # Its own savepoint. An IntegrityError marks the surrounding atomic
        # block unusable, and working out *which* constraint refused takes a
        # query - which would then fail instead of answering.
        with transaction.atomic():
            CouponClaim.objects.create(
                coupon=coupon,
                student=student,
                batch_scope=_claim_scope(coupon.batch),
            )
    except IntegrityError:
        if CouponClaim.objects.filter(coupon=coupon, student=student).exists():
            raise CouponError(
                "You have already claimed this coupon.", code="already_claimed"
            )
        raise CouponError(
            "You already have a coupon from this batch. Only one per student.",
            code="batch_limit_reached",
        )


def _take_use(coupon: Coupon) -> None:
    """Consume one use. The caller must already hold the row lock."""
    coupon.uses_taken += 1
    if coupon.remaining <= 0:
        coupon.status = Coupon.Status.FULLY_REDEEMED
    coupon.save(update_fields=["uses_taken", "status", "updated_at"])


def _give_use_back(coupon: Coupon) -> None:
    """Release one. A coupon that was full becomes usable again."""
    if coupon.uses_taken > 0:
        coupon.uses_taken -= 1
    if coupon.status == Coupon.Status.FULLY_REDEEMED and coupon.remaining > 0:
        coupon.status = Coupon.Status.ACTIVE
    coupon.save(update_fields=["uses_taken", "status", "updated_at"])


@transaction.atomic
def release_claim(*, redemption: CouponRedemption, actor=None) -> None:
    """
    Hand a held coupon back unspent.

    Without this a mis-scan burns a use of a shared code for good — the
    student cannot use it and nobody else can have it either, which is worse
    than the double-claim it is preventing.
    """
    locked = CouponRedemption.objects.select_for_update().get(pk=redemption.pk)

    if locked.status != CouponRedemption.Status.CLAIMED:
        raise CouponError(
            "That coupon has already been used.", code="already_used"
        )

    coupon = Coupon.objects.select_for_update().get(pk=locked.coupon_id)
    CouponClaim.objects.filter(coupon=coupon, student_id=locked.student_id).delete()
    locked.delete()
    _give_use_back(coupon)


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


@transaction.atomic
def apply(*, raw_code: str, fee_id: int, student, actor=None) -> CouponRedemption:
    """
    Spend it against a fee.

    Both rows are locked before anything is read, then every check runs
    again: claim-time validation proves nothing here, because the batch may
    have been revoked and the fee may have been paid in the meantime.
    """
    coupon = find_coupon(raw_code)
    coupon = Coupon.objects.select_for_update().select_related("batch").get(pk=coupon.pk)

    fee = Fee.objects.select_for_update().filter(pk=fee_id).first()
    if fee is None:
        raise CouponError("That fee no longer exists.", code="fee_not_found")

    check_usable(coupon, student)
    check_fee(coupon, fee, student)

    amount = quote(coupon, fee)
    if amount <= ZERO:
        raise CouponError("That fee has nothing left owing.", code="fee_settled")

    redemption = (
        CouponRedemption.objects.select_for_update()
        .filter(
            coupon=coupon, student=student, status=CouponRedemption.Status.CLAIMED
        )
        .first()
    )
    # A held coupon already took its use at claim time; only a straight
    # apply-without-claiming takes one now. Counting again here is what
    # would let a shared code pay out more times than it was issued for.
    took_use_now = redemption is None
    if redemption is None:
        # Spending one without claiming it first still has to respect both
        # limits, so it goes through the same rows the claim path writes.
        _record_claim(coupon, student)
        redemption = CouponRedemption(coupon=coupon, student=student)

    redemption.fee = fee
    redemption.amount = amount
    redemption.previous_discount_total = fee.discount_total
    redemption.previous_balance = fee.balance
    redemption.status = CouponRedemption.Status.APPLIED
    redemption.applied_at = timezone.now()
    redemption.updated_by = actor
    redemption.save()

    # Relief goes in its own column; `total_amount` is what was billed and
    # never moves. See api/fees/models.py.
    fee.discount_total += amount
    fee.balance -= amount
    if fee.balance <= ZERO:
        fee.balance = ZERO
        fee.status = Fee.FeeStatus.PAID
    fee.updated_by = actor
    fee.save(
        update_fields=[
            "discount_total",
            "balance",
            "status",
            "updated_by",
            "updated_at",
        ]
    )

    if took_use_now:
        _take_use(coupon)

    logger.info(
        "coupons.applied coupon=%s fee=%s student=%s amount=%s",
        coupon.code,
        fee.pk,
        student.pk,
        amount,
    )
    return redemption


# --------------------------------------------------------------------------
# Taking it back
# --------------------------------------------------------------------------


@transaction.atomic
def void_coupon(*, coupon: Coupon, actor, reason: str) -> Coupon:
    """Kill an unspent coupon. Nothing has moved, so nothing is restored."""
    if not (reason or "").strip():
        raise CouponError("A reason is required to void a coupon.", code="no_reason")

    locked = Coupon.objects.select_for_update().get(pk=coupon.pk)

    # `is_spent`, not `uses_taken`: a coupon somebody is merely holding has
    # taken a use but moved no money, and voiding it is exactly right — they
    # lose a coupon they had not used. Only one that reached a fee needs
    # reversing instead.
    if locked.is_spent:
        raise CouponError(
            "This coupon has already been used. Reverse the redemption "
            "instead — voiding would leave the discount in place.",
            code="already_redeemed",
        )

    if locked.status == Coupon.Status.REVOKED:
        return locked

    locked.status = Coupon.Status.REVOKED
    locked.revoked_by = actor
    locked.revoked_at = timezone.now()
    locked.revocation_reason = reason
    locked.updated_by = actor
    locked.save(
        update_fields=[
            "status",
            "revoked_by",
            "revoked_at",
            "revocation_reason",
            "updated_by",
            "updated_at",
        ]
    )
    return locked


@transaction.atomic
def revoke_batch(*, batch: CouponBatch, actor, reason: str) -> dict:
    """
    Recall a print run.

    Voids every unspent coupon and **leaves the spent ones alone**. The
    obvious implementation is a loop that reverses everything, and it would
    re-bill a dozen students who did nothing wrong. The spent ones are
    reported back so somebody can decide about them one at a time.
    """
    if not (reason or "").strip():
        raise CouponError("A reason is required to revoke a batch.", code="no_reason")

    locked = CouponBatch.objects.select_for_update().get(pk=batch.pk)

    # Spent means "reached a fee". One somebody is merely holding is voided
    # with the rest of the run — that is what recalling means — while one
    # that already discounted a fee is left for a human to decide about.
    spent_ids = Coupon.objects.filter(
        batch=locked, redemptions__status=CouponRedemption.Status.APPLIED
    ).values("pk")

    voided = (
        locked.coupons.exclude(pk__in=spent_ids)
        .exclude(status=Coupon.Status.REVOKED)
        .update(
            status=Coupon.Status.REVOKED,
            revoked_by=actor,
            revoked_at=timezone.now(),
            revocation_reason=reason,
            updated_at=timezone.now(),
        )
    )
    spent = locked.coupons.filter(pk__in=spent_ids).count()

    locked.status = CouponBatch.Status.REVOKED
    locked.revoked_by = actor
    locked.revoked_at = timezone.now()
    locked.revocation_reason = reason
    locked.updated_by = actor
    locked.save(
        update_fields=[
            "status",
            "revoked_by",
            "revoked_at",
            "revocation_reason",
            "updated_by",
            "updated_at",
        ]
    )

    logger.info(
        "coupons.batch.revoked batch=%s voided=%s left_spent=%s",
        locked.pk,
        voided,
        spent,
    )
    return {"voided": voided, "already_redeemed": spent}


@transaction.atomic
def reverse_redemption(
    *, redemption: CouponRedemption, actor, reason: str, allow_paid: bool = False
) -> CouponRedemption:
    """
    Undo a coupon that was already spent.

    This is the dangerous one. The relief comes back off the fee, which is
    fine while it is unpaid and creates a debt out of nowhere once it is not.
    A points discount can do the same, but there the student gets their
    points back and can re-spend them; a revoked coupon refunds nothing, so
    the student is simply poorer for a reason that was not their doing.

    Hence `allow_paid`: reversing against a settled fee is a separate,
    explicit decision, gated by its own permission at the view.
    """
    if not (reason or "").strip():
        raise CouponError(
            "A reason is required to reverse a redemption.", code="no_reason"
        )

    locked = CouponRedemption.objects.select_for_update().get(pk=redemption.pk)

    if locked.status == CouponRedemption.Status.REVERSED:
        raise CouponError("That redemption is already reversed.", code="already_reversed")

    if locked.status == CouponRedemption.Status.CLAIMED:
        # Never applied: release it rather than pretending to restore money.
        locked.status = CouponRedemption.Status.REVERSED
        locked.reversed_by = actor
        locked.reversed_at = timezone.now()
        locked.reversal_reason = reason
        locked.save()

        # Give the use back so somebody else can have it.
        held = Coupon.objects.select_for_update().get(pk=locked.coupon_id)
        CouponClaim.objects.filter(
            coupon=held, student_id=locked.student_id
        ).delete()
        _give_use_back(held)
        return locked

    fee = Fee.objects.select_for_update().get(pk=locked.fee_id)

    was_settled = fee.status == Fee.FeeStatus.PAID and fee.balance <= ZERO
    if was_settled and not allow_paid:
        raise CouponError(
            "That fee is already settled. Reversing puts the amount back as a "
            "debt the student has already been told they do not owe — it needs "
            "the paid-fee permission and a deliberate confirmation.",
            code="fee_already_paid",
        )

    fee.discount_total -= locked.amount
    if fee.discount_total < ZERO:
        fee.discount_total = ZERO
    fee.balance += locked.amount
    if fee.balance > ZERO and fee.status == Fee.FeeStatus.PAID:
        fee.status = Fee.FeeStatus.PENDING
    fee.updated_by = actor
    fee.save(
        update_fields=[
            "discount_total",
            "balance",
            "status",
            "updated_by",
            "updated_at",
        ]
    )

    locked.status = CouponRedemption.Status.REVERSED
    locked.reversed_by = actor
    locked.reversed_at = timezone.now()
    locked.reversal_reason = reason
    locked.updated_by = actor
    locked.save()

    coupon = Coupon.objects.select_for_update().get(pk=locked.coupon_id)
    # The claim goes back with the use. A reversal means the student received
    # nothing, so barring them from the rest of the run - which the batch
    # scope on this row would now do - would be punishing them for somebody
    # else's correction.
    CouponClaim.objects.filter(
        coupon=coupon, student_id=locked.student_id
    ).delete()
    _give_use_back(coupon)

    _notify_reversal(locked, fee, reason)
    _audit_reversal(locked, fee, actor)

    logger.info(
        "coupons.reversed redemption=%s fee=%s amount=%s",
        locked.pk,
        fee.pk,
        locked.amount,
    )
    return locked


def _notify_reversal(redemption: CouponRedemption, fee: Fee, reason: str) -> None:
    """
    Tell the student. Nobody should discover a restored debt by opening
    their fee list and finding a number that grew.
    """
    try:
        from api.notifications.models import Notification
        from api.notifications.service import notify_user

        user = getattr(redemption.student, "user", None)
        if user is None:
            return

        notify_user(
            user=user,
            title="A coupon discount was reversed",
            message=(
                f"The ₱{redemption.amount:,.2f} coupon discount on your "
                f"{fee.category.category_name} fee has been reversed, so that "
                f"amount is owed again. Reason: {reason}"
            ),
            type=Notification.NotificationType.SYSTEM_ANNOUNCEMENT,
            source=Notification.NotificationSource.COLLECTION,
            data={
                "fee_id": fee.pk,
                "amount": str(redemption.amount),
                "coupon": redemption.coupon.code,
            },
        )
    except Exception:
        logger.exception(
            "Could not notify student of coupon reversal %s", redemption.pk
        )


def _audit_reversal(redemption: CouponRedemption, fee: Fee, actor) -> None:
    try:
        from api.audits.models import AuditLog
        from api.audits.services import AuditLogService
        from api.audits.utils import resolve_actor

        actor_type, actor_id = resolve_actor(actor)
        AuditLogService.log_action(
            system=AuditLog.System.COLLECTION,
            entity_type="CouponRedemption",
            entity_id=redemption.pk,
            action=AuditLog.Action.CANCEL,
            actor_type=actor_type,
            actor_id=actor_id,
            old_values={
                "status": CouponRedemption.Status.APPLIED,
                "fee_discount_total": str(redemption.previous_discount_total),
                "fee_balance": str(redemption.previous_balance),
            },
            new_values={
                "status": CouponRedemption.Status.REVERSED,
                "fee_discount_total": str(fee.discount_total),
                "fee_balance": str(fee.balance),
                "reason": redemption.reversal_reason,
            },
        )
    except Exception:
        logger.exception("Could not audit coupon reversal %s", redemption.pk)


# --------------------------------------------------------------------------
# Housekeeping
# --------------------------------------------------------------------------


def expire_due_coupons() -> int:
    """
    Flip past-window coupons to EXPIRED.

    A sweep rather than a derived status, mirroring
    `fees/update-overdue-fees/`: deriving expiry on read makes every list
    query harder to reason about, and this way a coupon's row says what it
    is.
    """
    now = timezone.now()
    return Coupon.objects.filter(
        status=Coupon.Status.ACTIVE, batch__expires_at__lte=now
    ).update(status=Coupon.Status.EXPIRED, updated_at=now)
