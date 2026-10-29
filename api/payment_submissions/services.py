"""
Submission lifecycle transitions that move money.

Approve and cancel both mutate fee balances, so both run inside one
transaction with the submission row locked first. The lock is what makes
the status re-check meaningful: two staff approving (or cancelling) the
same submission at the same moment serialize on the row, and the loser
sees the new status and gets a clean error instead of creating duplicate
payments or restoring a balance twice.

Balance mutation itself is delegated to `api.payments.services` - the one
place that deducts and restores - so the amounts here can never drift from
the direct-payment paths.

Withdraw moves no money, but it competes for the same PENDING status as
approve, so it takes the same lock: a student withdrawing while staff
approve must resolve to exactly one of the two.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from api.payments import services as payment_services
from api.payments.models import Payment

from .models import PaymentSubmission, PaymentSubmissionItems


def approve_submission(*, submission, actor, remarks=""):
    """
    PENDING -> APPROVED: one GCash payment per item, each deducted under lock.

    Returns `(submission, items)` so the caller can audit-log the item
    amounts alongside the status change.

    Items CASCADE-delete with their fee, so a pending submission can have
    lost every item since it was created - approving that empty shell would
    be a silent no-op, hence the explicit guard.
    """
    with transaction.atomic():
        locked = PaymentSubmission.objects.select_for_update().get(
            pk=submission.pk
        )

        if locked.status != PaymentSubmission.SubmissionStatus.PENDING:
            raise serializers.ValidationError(
                {"detail": "Only pending submissions can be approved."}
            )

        items = list(
            PaymentSubmissionItems.objects.filter(payment_submission=locked)
            .select_related("fee")
            .order_by("pk")
        )
        if not items:
            raise serializers.ValidationError(
                {
                    "detail": (
                        "This submission has no fee items left to approve - "
                        "its fees may have been deleted. Reject it instead."
                    )
                }
            )

        for item in items:
            payment = payment_services.record_payment(
                fee=item.fee,
                amount=item.amount_paid,
                method=Payment.PaymentMethod.GCASH,
                actor=actor,
                submission=locked,
            )
            # The balance the money actually landed on, read under the fee
            # lock - not the stale figure captured when the student submitted.
            item.previous_balance = payment.previous_balance
            item.save(update_fields=["previous_balance", "updated_at"])

        locked.status = PaymentSubmission.SubmissionStatus.APPROVED
        locked.remarks = remarks
        locked.reviewed_by = actor
        locked.reviewed_at = timezone.now()
        locked.updated_by = actor
        locked.save(
            update_fields=[
                "status",
                "remarks",
                "reviewed_by",
                "reviewed_at",
                "updated_by",
                "updated_at",
            ]
        )

    return locked, items


def withdraw_submission(*, submission, actor, reason=""):
    """
    PENDING -> WITHDRAWN: the student recalls their own submission before
    review. No payments exist yet, so nothing is reversed - the lock and
    status re-check exist purely to lose cleanly against a staff member
    approving or rejecting at the same moment.

    The cancellation fields double as the withdrawal record; cancelled_by
    being the student's own user is what keeps a withdrawal
    distinguishable from a staff cancellation. The reason is optional -
    a student pulling back their own mistake owes nobody an essay.
    """
    with transaction.atomic():
        locked = PaymentSubmission.objects.select_for_update().get(
            pk=submission.pk
        )

        if locked.status != PaymentSubmission.SubmissionStatus.PENDING:
            raise serializers.ValidationError(
                {"detail": "Only pending submissions can be withdrawn."}
            )

        locked.status = PaymentSubmission.SubmissionStatus.WITHDRAWN
        locked.cancelled_by = actor
        locked.cancelled_at = timezone.now()
        locked.cancellation_reason = (reason or "").strip()
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


def cancel_submission(*, submission, actor, reason):
    """
    APPROVED -> CANCELLED: reverse every active payment, restore each fee's
    balance, and record who/when/why. The review fields are left untouched -
    the approval happened, and cancelling it is history, not erasure.

    Returns `(submission, cancelled_payments)` for the caller's audit log.
    """
    if not (reason or "").strip():
        raise serializers.ValidationError(
            {"remarks": "Remarks are required to cancel a submission."}
        )

    with transaction.atomic():
        locked = PaymentSubmission.objects.select_for_update().get(
            pk=submission.pk
        )

        if locked.status != PaymentSubmission.SubmissionStatus.APPROVED:
            raise serializers.ValidationError(
                {"detail": "Only approved submissions can be cancelled."}
            )

        cancelled_payments = [
            payment_services.cancel_payment(
                payment=payment,
                actor=actor,
                reason=reason,
                cascading_from_submission=True,
            )
            for payment in locked.payments.filter(
                status=Payment.PaymentStatus.ACTIVE
            ).order_by("pk")
        ]

        locked.status = PaymentSubmission.SubmissionStatus.CANCELLED
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

    return locked, cancelled_payments
