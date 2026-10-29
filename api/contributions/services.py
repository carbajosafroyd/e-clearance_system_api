"""
Every point that moves, moves through here.

Two invariants this module exists to keep:

1. **A balance is always explainable.** `apply_transaction` is the only thing
   that writes `PointAccount.balance`, and it writes the matching
   `PointTransaction` in the same locked block. So
   `balance == total_earned - total_spent` always holds, and the ledger adds
   up to it.

2. **A discount never invents money.** Approving one raises the fee's
   `discount_total` and lowers its `balance` by the same amount; cancelling
   restores exactly what it took and refunds the points. Reports derive
   `collected` as `total_amount - discount_total - balance`, so moving the
   balance without recording the relief would have booked a discount as cash
   received - and the collection report would have read the drawdown as
   revenue given away unrecorded.

Both approve paths take a row lock and re-check the status inside it, so two
reviewers clicking at the same moment serialise instead of double-paying.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_CEILING
from typing import Optional

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from api.fees import cache as fee_cache
from api.fees.models import Fee
from core import features

from .models import (
    Contribution,
    FeeDiscount,
    PointAccount,
    PointPolicy,
    PointTransaction,
    default_awards,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def get_policy(*, institute_id: int, system: str) -> PointPolicy:
    """The policy for one institute+system, created with defaults if absent.

    Auto-creating rather than failing is deliberate: a new system should
    accept reports the day it is added, not the day somebody remembers to
    open the admin. The defaults are conservative and visible in the API, so
    a coordinator can see and change them.
    """
    policy, created = PointPolicy.objects.get_or_create(
        institute_id=institute_id,
        system=system,
        defaults={"awards": default_awards()},
    )
    if created:
        logger.info(
            "contributions.policy.created institute=%s system=%s",
            institute_id,
            system,
        )
    return policy


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def get_account(*, student, system: str) -> PointAccount:
    account, _ = PointAccount.objects.get_or_create(student=student, system=system)
    return account


@transaction.atomic
def apply_transaction(
    *,
    student,
    system: str,
    delta: int,
    reason: str,
    actor=None,
    contribution: Optional[Contribution] = None,
    fee_discount: Optional[FeeDiscount] = None,
    remarks: str = "",
) -> PointTransaction:
    """Move `delta` points and record why. The only writer of `balance`."""
    if delta == 0:
        raise serializers.ValidationError({"detail": "A transaction cannot be zero."})

    account = get_account(student=student, system=system)
    locked = PointAccount.objects.select_for_update().get(pk=account.pk)

    new_balance = locked.balance + delta
    if new_balance < 0:
        raise serializers.ValidationError(
            {
                "detail": (
                    f"Not enough points: {locked.balance} available, "
                    f"{abs(delta)} needed."
                )
            }
        )

    locked.balance = new_balance
    if delta > 0:
        locked.total_earned += delta
    else:
        locked.total_spent += abs(delta)
    locked.save(update_fields=["balance", "total_earned", "total_spent", "updated_at"])

    entry = PointTransaction.objects.create(
        account=locked,
        delta=delta,
        balance_after=new_balance,
        reason=reason,
        remarks=remarks or None,
        contribution=contribution,
        fee_discount=fee_discount,
        created_by=actor,
    )

    logger.info(
        "contributions.points student=%s system=%s delta=%s reason=%s balance=%s",
        student.pk,
        system,
        delta,
        reason,
        new_balance,
    )
    return entry


# ---------------------------------------------------------------------------
# Contribution review
# ---------------------------------------------------------------------------


def suggested_award(contribution: Contribution) -> int:
    policy = get_policy(
        institute_id=contribution.student.program.institute_id,
        system=contribution.system,
    )
    return policy.award_for(contribution.kind, contribution.impact)


@transaction.atomic
def approve_contribution(
    *,
    contribution: Contribution,
    actor,
    points: Optional[int] = None,
    remarks: str = "",
    impact: Optional[str] = None,
) -> Contribution:
    """Accept a report and pay for it.

    `points` lets the reviewer override the policy's suggestion - a report
    can be worth more than its category implies. Left out, the policy decides
    from the kind and (possibly corrected) impact.
    """
    locked = (
        Contribution.objects.select_for_update()
        .select_related("student__program")
        .get(pk=contribution.pk)
    )

    if locked.status in Contribution.TERMINAL_STATUSES:
        raise serializers.ValidationError(
            {"detail": f"This contribution is already {locked.status}."}
        )

    if impact:
        locked.impact = impact

    if features.is_enabled(features.CONTRIBUTION_POINTS):
        policy = get_policy(
            institute_id=locked.student.program.institute_id, system=locked.system
        )
        if not policy.is_active:
            raise serializers.ValidationError(
                {
                    "detail": (
                        "The points programme for this system is switched "
                        "off; no award can be made."
                    )
                }
            )

        award = (
            policy.award_for(locked.kind, locked.impact) if points is None else points
        )
        if award < 0:
            raise serializers.ValidationError({"points": "Points cannot be negative."})
    else:
        # Awards are switched off for this deployment: reports are still
        # filed, read and accepted, they just do not pay. Approving is the
        # useful half of the loop while the scheme is being trialled, so it
        # stays - silently at zero rather than refused.
        award = 0

    locked.status = Contribution.Status.APPROVED
    locked.points_awarded = award
    locked.reviewed_by = actor
    locked.reviewed_at = timezone.now()
    locked.review_remarks = remarks or None
    locked.updated_by = actor
    locked.save()

    if award:
        apply_transaction(
            student=locked.student,
            system=locked.system,
            delta=award,
            reason=PointTransaction.Reason.CONTRIBUTION_APPROVED,
            actor=actor,
            contribution=locked,
            remarks=f"Approved: {locked.title}",
        )

    return locked


@transaction.atomic
def reject_contribution(
    *, contribution: Contribution, actor, remarks: str, duplicate: bool = False
) -> Contribution:
    if not (remarks or "").strip():
        raise serializers.ValidationError(
            {"remarks": "Tell the student why. Remarks are required to reject."}
        )

    locked = Contribution.objects.select_for_update().get(pk=contribution.pk)
    if locked.status in Contribution.TERMINAL_STATUSES:
        raise serializers.ValidationError(
            {"detail": f"This contribution is already {locked.status}."}
        )

    locked.status = (
        Contribution.Status.DUPLICATE if duplicate else Contribution.Status.REJECTED
    )
    locked.points_awarded = 0
    locked.reviewed_by = actor
    locked.reviewed_at = timezone.now()
    locked.review_remarks = remarks
    locked.updated_by = actor
    locked.save()
    return locked


@transaction.atomic
def revoke_contribution(
    *, contribution: Contribution, actor, remarks: str
) -> Contribution:
    """Undo an approval and claw the points back.

    Refuses when the student has already spent below what was awarded: the
    points are gone into a discount, and taking them back would leave a
    negative balance. Cancel the discount first.
    """
    if not (remarks or "").strip():
        raise serializers.ValidationError(
            {"remarks": "Remarks are required to revoke an approval."}
        )

    locked = (
        Contribution.objects.select_for_update()
        .select_related("student")
        .get(pk=contribution.pk)
    )
    if locked.status != Contribution.Status.APPROVED:
        raise serializers.ValidationError(
            {"detail": "Only approved contributions can be revoked."}
        )

    awarded = locked.points_awarded

    locked.status = Contribution.Status.REJECTED
    locked.points_awarded = 0
    locked.reviewed_by = actor
    locked.reviewed_at = timezone.now()
    locked.review_remarks = remarks
    locked.updated_by = actor
    locked.save()

    if awarded:
        apply_transaction(
            student=locked.student,
            system=locked.system,
            delta=-awarded,
            reason=PointTransaction.Reason.CONTRIBUTION_REVOKED,
            actor=actor,
            contribution=locked,
            remarks=remarks,
        )

    return locked


@transaction.atomic
def adjust_points(*, student, system: str, delta: int, actor, remarks: str):
    """Manual correction, for the cases the workflow does not cover."""
    if not (remarks or "").strip():
        raise serializers.ValidationError(
            {"remarks": "Remarks are required for a manual adjustment."}
        )
    return apply_transaction(
        student=student,
        system=system,
        delta=delta,
        reason=PointTransaction.Reason.ADJUSTMENT,
        actor=actor,
        remarks=remarks,
    )


# ---------------------------------------------------------------------------
# Redemption
# ---------------------------------------------------------------------------


def discount_quote(*, fee: Fee, system: str, points: int) -> dict:
    """What `points` would be worth against `fee`, and whether it is allowed.

    Used by the request serializer and exposed on its own so the UI can show
    a live "N points = PHP X" as the student drags the slider.
    """
    institute_id = fee.student.program.institute_id
    policy = get_policy(institute_id=institute_id, system=system)
    account = PointAccount.objects.filter(student=fee.student, system=system).first()
    available = account.balance if account else 0

    value = policy.peso_value(points)
    ceiling = (fee.total_amount * policy.max_discount_percent / Decimal("100")).quantize(
        Decimal("0.01")
    )
    # Never more than is still owed - a discount is not a refund.
    max_amount = min(ceiling, fee.balance)

    return {
        "system": system,
        "points": points,
        "amount": value,
        "available_points": available,
        "peso_per_point": policy.peso_per_point,
        "min_points": policy.min_points_per_redemption,
        "max_points": policy.max_points_per_redemption,
        "max_discount_percent": policy.max_discount_percent,
        "max_amount": max_amount,
        "policy_active": policy.is_active,
    }


def validate_redemption(*, fee: Fee, system: str, points: int) -> Decimal:
    """Check a requested redemption and return the peso amount it buys."""
    quote = discount_quote(fee=fee, system=system, points=points)

    if not quote["policy_active"]:
        raise serializers.ValidationError(
            {"detail": "The points programme for this system is switched off."}
        )
    if fee.status not in (Fee.FeeStatus.PENDING, Fee.FeeStatus.OVERDUE):
        raise serializers.ValidationError(
            {"detail": f"Only pending or overdue fees can be discounted (this one is {fee.status})."}
        )
    if fee.balance <= 0:
        raise serializers.ValidationError(
            {"detail": "This fee has nothing left owing."}
        )
    if points < quote["min_points"]:
        raise serializers.ValidationError(
            {"points": f"At least {quote['min_points']} points are needed."}
        )
    if points > quote["max_points"]:
        raise serializers.ValidationError(
            {"points": f"At most {quote['max_points']} points may be spent at once."}
        )
    if points > quote["available_points"]:
        raise serializers.ValidationError(
            {
                "points": (
                    f"You have {quote['available_points']} point(s) in the "
                    f"{system} system."
                )
            }
        )

    amount = quote["amount"]
    if amount > quote["max_amount"]:
        raise serializers.ValidationError(
            {
                "points": (
                    f"That is worth PHP {amount}, more than the PHP "
                    f"{quote['max_amount']} this fee allows."
                )
            }
        )
    if amount <= ZERO:
        raise serializers.ValidationError(
            {"points": "That many points are worth nothing at the current rate."}
        )

    return amount


@transaction.atomic
def request_discount(*, fee: Fee, student, system: str, points: int, actor, remarks=""):
    """Reserve points against a fee, pending an admin's approval.

    Points leave the wallet **now**, not on approval: otherwise a student
    could queue five requests against the same balance and have them all
    approved. A rejection or cancellation puts them straight back.
    """
    if fee.student_id != student.pk:
        raise serializers.ValidationError(
            {"detail": "That fee belongs to another student."}
        )

    amount = validate_redemption(fee=fee, system=system, points=points)

    discount = FeeDiscount.objects.create(
        fee=fee,
        student=student,
        system=system,
        points_spent=points,
        amount=amount,
        status=FeeDiscount.Status.PENDING,
        requested_by=actor,
        remarks=remarks or None,
        updated_by=actor,
    )

    apply_transaction(
        student=student,
        system=system,
        delta=-points,
        reason=PointTransaction.Reason.REDEMPTION,
        actor=actor,
        fee_discount=discount,
        remarks=f"Requested against fee #{fee.pk}",
    )

    return discount


def _unused_points(*, fee: Fee, discount: FeeDiscount, applied: Decimal) -> int:
    """How many of the reserved points the capped amount did not buy.

    Rounded so the points kept always cover the relief given: charging a
    fraction of a point is not possible, so the student keeps the whole point
    only when none of it was needed. Never returns more than was spent, and
    never turns a rounding artefact into free points.
    """
    institute_id = fee.student.program.institute_id
    policy = get_policy(institute_id=institute_id, system=discount.system)
    rate = policy.peso_per_point

    if rate <= ZERO:
        # A zero rate means the amount was not derived from the points at all;
        # there is no defensible split, so nothing is returned.
        return 0

    used = int((Decimal(applied) / Decimal(rate)).to_integral_value(ROUND_CEILING))
    return max(0, min(discount.points_spent, discount.points_spent - used))


@transaction.atomic
def approve_discount(*, discount: FeeDiscount, actor, remarks: str = "") -> FeeDiscount:
    """Apply the discount to the fee.

    Raises `discount_total` and lowers `balance` by the same amount, so the
    reporting identity `collected = total_amount - discount_total - balance`
    is untouched and `total_amount` keeps saying what was billed. The amount
    is capped at what is still owed, in case a payment landed after the
    request.
    """
    locked = FeeDiscount.objects.select_for_update().get(pk=discount.pk)
    if locked.status != FeeDiscount.Status.PENDING:
        raise serializers.ValidationError(
            {"detail": f"This discount is already {locked.status}."}
        )

    fee = Fee.objects.select_for_update().get(pk=locked.fee_id)
    if fee.status not in (Fee.FeeStatus.PENDING, Fee.FeeStatus.OVERDUE):
        raise serializers.ValidationError(
            {"detail": f"Fee #{fee.pk} is {fee.status}; nothing to discount."}
        )

    applied = min(locked.amount, fee.balance)
    if applied <= ZERO:
        raise serializers.ValidationError(
            {"detail": f"Fee #{fee.pk} has nothing left owing."}
        )

    # The cap is not free. Points left the wallet when the request was made,
    # priced against the balance at that moment; if a payment or a coupon has
    # since shrunk the fee, approving at the capped amount without giving the
    # difference back would quietly charge the student for relief they never
    # received. Refunded here rather than left for someone to notice, because
    # nothing in the ledger would show it had happened.
    refunded_points = 0
    if applied < locked.amount:
        refunded_points = _unused_points(
            fee=fee, discount=locked, applied=applied
        )

    # `previous_total_amount` is kept for the audit trail and for rows
    # written before relief had its own column; the undo below works off
    # `amount`, which is the only figure that survives a partial cap.
    locked.previous_total_amount = fee.total_amount
    locked.previous_balance = fee.balance

    fee.discount_total += applied
    fee.balance -= applied
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

    locked.amount = applied
    locked.status = FeeDiscount.Status.APPROVED
    locked.reviewed_by = actor
    locked.reviewed_at = timezone.now()
    if remarks:
        locked.remarks = remarks
    locked.updated_by = actor

    if refunded_points:
        locked.points_spent -= refunded_points
        apply_transaction(
            student=locked.student,
            system=locked.system,
            delta=refunded_points,
            reason=PointTransaction.Reason.REDEMPTION_ADJUSTED,
            actor=actor,
            fee_discount=locked,
            remarks=(
                f"Fee #{fee.pk} had less owing than when the request was "
                f"made; {refunded_points} point(s) returned"
            ),
        )

    locked.save()

    fee_cache.invalidate_after_commit(fee_cache.LIST_NS)

    logger.info(
        "contributions.discount.approved discount=%s fee=%s amount=%s points=%s",
        locked.pk,
        fee.pk,
        applied,
        locked.points_spent,
    )
    return locked


@transaction.atomic
def reject_discount(*, discount: FeeDiscount, actor, remarks: str) -> FeeDiscount:
    """Refuse the request and put the points back."""
    if not (remarks or "").strip():
        raise serializers.ValidationError(
            {"remarks": "Remarks are required to reject a discount request."}
        )

    locked = FeeDiscount.objects.select_for_update().get(pk=discount.pk)
    if locked.status != FeeDiscount.Status.PENDING:
        raise serializers.ValidationError(
            {"detail": f"This discount is already {locked.status}."}
        )

    locked.status = FeeDiscount.Status.REJECTED
    locked.reviewed_by = actor
    locked.reviewed_at = timezone.now()
    locked.remarks = remarks
    locked.updated_by = actor
    locked.save()

    _refund(locked, actor=actor, remarks=f"Request rejected: {remarks}")
    return locked


@transaction.atomic
def cancel_discount(*, discount: FeeDiscount, actor, reason: str) -> FeeDiscount:
    """Undo an approved discount: restore the fee, refund the points."""
    if not (reason or "").strip():
        raise serializers.ValidationError(
            {"reason": "A reason is required to cancel a discount."}
        )

    locked = FeeDiscount.objects.select_for_update().get(pk=discount.pk)

    if locked.status == FeeDiscount.Status.PENDING:
        # Never applied to the fee - just release the reservation.
        locked.status = FeeDiscount.Status.CANCELLED
        locked.cancelled_by = actor
        locked.cancelled_at = timezone.now()
        locked.cancellation_reason = reason
        locked.updated_by = actor
        locked.save()
        _refund(locked, actor=actor, remarks=f"Request cancelled: {reason}")
        return locked

    if locked.status != FeeDiscount.Status.APPROVED:
        raise serializers.ValidationError(
            {"detail": f"A {locked.status} discount cannot be cancelled."}
        )

    fee = Fee.objects.select_for_update().get(pk=locked.fee_id)

    fee.discount_total -= locked.amount
    if fee.discount_total < ZERO:
        # Belt and braces: a negative would trip the check constraint and
        # take a legitimate cancellation down with it.
        fee.discount_total = ZERO
    fee.balance += locked.amount
    if fee.balance > ZERO and fee.status == Fee.FeeStatus.PAID:
        # It was only PAID because the discount cleared it; the overdue sweep
        # re-flags it if the due date has since passed.
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

    locked.status = FeeDiscount.Status.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = timezone.now()
    locked.cancellation_reason = reason
    locked.updated_by = actor
    locked.save()

    _refund(locked, actor=actor, remarks=f"Discount cancelled: {reason}")
    fee_cache.invalidate_after_commit(fee_cache.LIST_NS)

    logger.info(
        "contributions.discount.cancelled discount=%s fee=%s restored=%s",
        locked.pk,
        fee.pk,
        locked.amount,
    )
    return locked


def _refund(discount: FeeDiscount, *, actor, remarks: str) -> None:
    apply_transaction(
        student=discount.student,
        system=discount.system,
        delta=discount.points_spent,
        reason=PointTransaction.Reason.REDEMPTION_REVERSED,
        actor=actor,
        fee_discount=discount,
        remarks=remarks,
    )
