"""
Student-facing notifications for the submission review lifecycle.

Every review outcome (approved, rejected, cancelled) changes what the
student owes - or tells them their money didn't land - without them doing
anything, so each notification carries the whole story: which submission
(reference number), the fees involved item by item, what each balance
became, why, and what the student should do or expect next. Sends are
best-effort - a notification failure must never undo or hide a review
decision that already happened.
"""

from __future__ import annotations

import logging
import traceback
from decimal import Decimal

from api.notifications.service import notify_user
from api.payments.models import Payment

from .models import PaymentSubmissionItems

logger = logging.getLogger(__name__)

NOTIFICATION_SOURCE = "collection"
SUBMISSION_APPROVED_TYPE = "payment_submission_approved"
SUBMISSION_REJECTED_TYPE = "payment_submission_rejected"
SUBMISSION_CANCELLED_TYPE = "payment_submission_cancelled"


def notify_submission_approved(*, submission, actor) -> None:
    """Send the student a receipt for their approved submission.

    `submission` must already be APPROVED with its payments recorded - the
    message reads the applied amounts and settled balances off the rows.
    """
    try:
        recipient = submission.student.user
        if recipient is None:
            return  # student has no portal account to notify

        # The payments the approval just created, with their (already
        # deducted) fees, in one query. previous_balance on each payment is
        # the balance its money actually landed on, read under the fee lock.
        payments = list(
            submission.payments.filter(status=Payment.PaymentStatus.ACTIVE)
            .select_related("fee__category")
            .order_by("pk")
        )
        total_applied = sum(
            (p.amount_paid for p in payments), Decimal("0.00")
        )

        # Balance after each payment is derived from the payment row, not
        # read off the fee: two items on the same fee would otherwise both
        # show the final figure instead of what each one left behind. A
        # derived zero also proves the fee was settled at that moment.
        applied_lines = "\n".join(
            f"• {p.fee.category.category_name} "
            f"({p.fee.academic_year}, {p.fee.get_semester_display()}): "
            f"₱{p.amount_paid:,.2f} paid — balance ₱{p.previous_balance:,.2f} "
            f"→ ₱{p.previous_balance - p.amount_paid:,.2f}"
            + (" (Paid)" if p.previous_balance - p.amount_paid <= 0 else "")
            for p in payments
        )

        message = (
            f"Good news! Your GCash payment submission "
            f"(Ref. No. {submission.reference_number}) has been approved "
            f"and your payment has been applied to your fees.\n"
            f"\n"
            f"Approved submission details:\n"
            f"• Submission No.: {submission.pk}\n"
            f"• Reference No.: {submission.reference_number}\n"
            f"• Total applied: ₱{total_applied:,.2f}\n"
            f"• Submitted on: {submission.created_at:%b %d, %Y}\n"
            f"• Approved on: {submission.reviewed_at:%b %d, %Y}\n"
        )
        if (submission.remarks or "").strip():
            message += f"• Remarks: {submission.remarks}\n"
        if applied_lines:
            message += f"\nPayments applied to your fees:\n{applied_lines}\n"
        message += (
            f"\n"
            f"Thank you for your payment! Keep this notification as your "
            f"record — it lists exactly where every peso of your "
            f"₱{total_applied:,.2f} went."
        )

        notify_user(
            user=recipient,
            sender=actor,
            title=(
                f"Payment Submission Approved — "
                f"₱{total_applied:,.2f} Applied to Your Fees"
            ),
            message=message,
            type=SUBMISSION_APPROVED_TYPE,
            source=NOTIFICATION_SOURCE,
            data={
                "submission_id": submission.pk,
                "reference_number": submission.reference_number,
                "total_applied": str(total_applied),
                "submitted_at": submission.created_at.isoformat()
                if submission.created_at
                else None,
                "approved_at": submission.reviewed_at.isoformat()
                if submission.reviewed_at
                else None,
                "remarks": submission.remarks or "",
                "payments_applied": [
                    {
                        "payment_id": p.pk,
                        "fee_id": p.fee_id,
                        "category": p.fee.category.category_name,
                        "academic_year": p.fee.academic_year,
                        "semester": p.fee.semester,
                        "amount_paid": str(p.amount_paid),
                        "previous_balance": str(p.previous_balance),
                        "new_balance": str(p.previous_balance - p.amount_paid),
                        "fee_status": p.fee.status,
                    }
                    for p in payments
                ],
            },
        )
    except Exception:
        logger.error(
            "Failed to notify student of submission approval\n%s",
            traceback.format_exc(),
        )


def notify_submission_rejected(*, submission, actor) -> None:
    """Tell the student their submission was rejected, and what to do next.

    `submission` must already be REJECTED with the reviewer's remarks
    saved - the message reads them off the row. No balances moved, and the
    message says so explicitly.
    """
    try:
        recipient = submission.student.user
        if recipient is None:
            return  # student has no portal account to notify

        items = list(
            PaymentSubmissionItems.objects.filter(payment_submission=submission)
            .select_related("fee__category")
            .order_by("pk")
        )

        item_lines = "\n".join(
            f"• {item.fee.category.category_name} "
            f"({item.fee.academic_year}, {item.fee.get_semester_display()}): "
            f"₱{item.amount_paid:,.2f}"
            for item in items
        )

        message = (
            f"Your GCash payment submission "
            f"(Ref. No. {submission.reference_number}) has been reviewed "
            f"and could not be approved.\n"
            f"\n"
            f"Rejected submission details:\n"
            f"• Submission No.: {submission.pk}\n"
            f"• Reference No.: {submission.reference_number}\n"
            f"• Total amount: ₱{submission.total_amount_paid:,.2f}\n"
            f"• Submitted on: {submission.created_at:%b %d, %Y}\n"
            f"• Reviewed on: {submission.reviewed_at:%b %d, %Y}\n"
            f"• Reason: {submission.remarks}\n"
        )
        if item_lines:
            message += f"\nFees this submission was for:\n{item_lines}\n"
        message += (
            f"\n"
            f"What this means:\n"
            f"• No payment was applied — your fee balances are unchanged.\n"
            f"• If you already sent ₱{submission.total_amount_paid:,.2f} "
            f"through GCash, don't worry — your money is not lost. Please "
            f"contact the collection office with your reference number so "
            f"it can be verified, or submit a new payment submission with "
            f"the corrected details."
        )

        notify_user(
            user=recipient,
            sender=actor,
            title=(
                f"Payment Submission Rejected — "
                f"Ref. No. {submission.reference_number}"
            ),
            message=message,
            type=SUBMISSION_REJECTED_TYPE,
            source=NOTIFICATION_SOURCE,
            data={
                "submission_id": submission.pk,
                "reference_number": submission.reference_number,
                "total_amount": str(submission.total_amount_paid),
                "submitted_at": submission.created_at.isoformat()
                if submission.created_at
                else None,
                "reviewed_at": submission.reviewed_at.isoformat()
                if submission.reviewed_at
                else None,
                "reason": submission.remarks or "",
                "items": [
                    {
                        "fee_id": item.fee_id,
                        "category": item.fee.category.category_name,
                        "academic_year": item.fee.academic_year,
                        "semester": item.fee.semester,
                        "amount": str(item.amount_paid),
                    }
                    for item in items
                ],
            },
        )
    except Exception:
        logger.error(
            "Failed to notify student of submission rejection\n%s",
            traceback.format_exc(),
        )


def notify_submission_cancelled(*, submission, cancelled_payments, actor) -> None:
    """Tell the student everything about one cancelled submission.

    `submission` must already be CANCELLED and every payment in
    `cancelled_payments` already reversed - the message reads the
    post-cancellation state off the rows.
    """
    try:
        recipient = submission.student.user
        if recipient is None:
            return  # student has no portal account to notify

        # Re-read the reversed payments with their (restored) fees in one
        # query, instead of lazy-loading a fee per payment below.
        payments = list(
            Payment.objects.filter(pk__in=[p.pk for p in cancelled_payments])
            .select_related("fee__category")
            .order_by("pk")
        )
        total_returned = sum(
            (p.amount_paid for p in payments), Decimal("0.00")
        )

        reversal_lines = "\n".join(
            f"• {p.fee.category.category_name} "
            f"({p.fee.academic_year}, {p.fee.get_semester_display()}): "
            f"₱{p.amount_paid:,.2f} returned — balance is back to "
            f"₱{p.fee.balance:,.2f} of ₱{p.fee.total_amount:,.2f} "
            f"({p.fee.get_status_display()})"
            for p in payments
        )

        message = (
            f"Your approved GCash payment submission "
            f"(Ref. No. {submission.reference_number}) has been cancelled "
            f"by the collection office.\n"
            f"\n"
            f"Cancelled submission details:\n"
            f"• Submission No.: {submission.pk}\n"
            f"• Reference No.: {submission.reference_number}\n"
            f"• Total amount: ₱{total_returned:,.2f}\n"
            f"• Submitted on: {submission.created_at:%b %d, %Y}\n"
            f"• Cancelled on: {submission.cancelled_at:%b %d, %Y}\n"
            f"• Reason: {submission.cancellation_reason}\n"
        )
        if reversal_lines:
            message += (
                f"\n"
                f"Payments reversed by this cancellation:\n"
                f"{reversal_lines}\n"
            )
        message += (
            f"\n"
            f"Rest assured — the full total of ₱{total_returned:,.2f} from "
            f"this submission has been returned to your fee balance(s), so "
            f"nothing is lost from your account. If you already sent this "
            f"amount through GCash or believe this cancellation was a "
            f"mistake, please contact the collection office and it will be "
            f"sorted out for you."
        )

        notify_user(
            user=recipient,
            sender=actor,
            title=(
                f"Payment Submission Cancelled — "
                f"₱{total_returned:,.2f} Returned to Your Balance"
            ),
            message=message,
            type=SUBMISSION_CANCELLED_TYPE,
            source=NOTIFICATION_SOURCE,
            data={
                "submission_id": submission.pk,
                "reference_number": submission.reference_number,
                "total_returned": str(total_returned),
                "submitted_at": submission.created_at.isoformat()
                if submission.created_at
                else None,
                "cancelled_at": submission.cancelled_at.isoformat()
                if submission.cancelled_at
                else None,
                "cancellation_reason": submission.cancellation_reason,
                "cancelled_payments": [
                    {
                        "payment_id": p.pk,
                        "fee_id": p.fee_id,
                        "category": p.fee.category.category_name,
                        "academic_year": p.fee.academic_year,
                        "semester": p.fee.semester,
                        "amount_returned": str(p.amount_paid),
                        "restored_balance": str(p.fee.balance),
                        "fee_total_amount": str(p.fee.total_amount),
                        "fee_status": p.fee.status,
                    }
                    for p in payments
                ],
            },
        )
    except Exception:
        logger.error(
            "Failed to notify student of submission cancellation\n%s",
            traceback.format_exc(),
        )
