"""
The one place that moves money.

Every path that applies a payment to a fee, or reverses one, goes through
these two functions - the direct payment endpoint, the bulk endpoint, the
submission approve action and the two cancel actions. Both take a row lock
on the fee (and the payment, when reversing) before touching a balance, so
two staff clicking the same button at the same moment serialize instead of
double-deducting or double-restoring.

Corrections are reversals, never edits: a Payment row is immutable once
written, and an encoding mistake is undone by `cancel_payment`, which flips
the row to CANCELLED and puts the money back on the fee. That is also why
the fee-derived report numbers (total_amount - balance) self-correct on
cancellation without knowing payments have a status at all.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from api.fees.models import Fee

from .models import Payment

ZERO = Decimal("0.00")


def record_payment(*, fee, amount, method, actor, submission=None) -> Payment:
    """
    Turn received money into a Payment row and draw the fee's balance down.

    The fee is re-read under `select_for_update`, and the amount is validated
    against that locked balance - not against whatever stale copy the caller
    was holding. Two items for the same fee in one submission therefore each
    see the balance the previous one left behind.
    """
    with transaction.atomic():
        locked_fee = Fee.objects.select_for_update().get(pk=fee.pk)

        _validate_payable(locked_fee, amount)

        payment = Payment.objects.create(
            fee=locked_fee,
            previous_balance=locked_fee.balance,
            amount_paid=amount,
            payment_method=method,
            payment_submission=submission,
            received_by=actor,
            updated_by=actor,
        )

        locked_fee.balance -= amount
        # <= 0, not < 1: validation already guarantees the balance cannot go
        # negative, and "< 1" silently forgave any remainder under a peso.
        if locked_fee.balance <= ZERO:
            locked_fee.balance = ZERO
            locked_fee.status = Fee.FeeStatus.PAID
        locked_fee.updated_by = actor
        locked_fee.save(
            update_fields=["balance", "status", "updated_by", "updated_at"]
        )

    return payment


def cancel_payment(
    *, payment, actor, reason, cascading_from_submission=False
) -> Payment:
    """
    Reverse one payment: flip it to CANCELLED and restore the fee's balance.

    Submission-born payments refuse to be cancelled alone - they have sibling
    payments and an approved submission behind them, so the submission cancel
    action (which passes `cascading_from_submission=True`) is the only caller
    allowed to reverse them.
    """
    if not (reason or "").strip():
        raise serializers.ValidationError(
            {"reason": "A cancellation reason is required."}
        )

    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)

        if locked.status == Payment.PaymentStatus.CANCELLED:
            raise serializers.ValidationError(
                {"detail": "This payment is already cancelled."}
            )
        if locked.payment_submission_id and not cascading_from_submission:
            raise serializers.ValidationError(
                {
                    "detail": (
                        "This payment belongs to a payment submission - "
                        "cancel the submission instead."
                    )
                }
            )

        fee = Fee.objects.select_for_update().get(pk=locked.fee_id)
        fee.balance += locked.amount_paid
        # A fee the payment had settled is owed again. WAIVED is left alone -
        # the money comes back onto the balance but the forgiveness stands -
        # and the overdue automation re-flags past-due PENDING fees on its own.
        if fee.status == Fee.FeeStatus.PAID and fee.balance > ZERO:
            fee.status = Fee.FeeStatus.PENDING
        fee.updated_by = actor
        fee.save(update_fields=["balance", "status", "updated_by", "updated_at"])

        locked.status = Payment.PaymentStatus.CANCELLED
        locked.cancelled_by = actor
        locked.cancelled_at = timezone.now()
        locked.cancellation_reason = reason
        locked.updated_by = actor
        locked.save(
            update_fields=[
                "status",
                "cancelled_by",
                "cancelled_at",
                "cancellation_reason",
                "updated_by",
                "updated_at",
            ]
        )

    return locked


def _validate_payable(locked_fee: Fee, amount) -> None:
    """Refuse anything the locked fee cannot absorb."""
    if amount is None or amount <= ZERO:
        raise serializers.ValidationError(
            {
                "amount_paid": (
                    f"Amount must be greater than 0 for Fee ID {locked_fee.pk}."
                )
            }
        )
    if locked_fee.status == Fee.FeeStatus.WAIVED:
        raise serializers.ValidationError(
            {
                "fee": (
                    f"Fee ID {locked_fee.pk} is waived and cannot receive "
                    "payments. Unwaive it first."
                )
            }
        )
    if amount > locked_fee.balance:
        raise serializers.ValidationError(
            {
                "amount_paid": (
                    f"Amount {amount} exceeds remaining balance "
                    f"{locked_fee.balance} for Fee ID {locked_fee.pk}."
                )
            }
        )
