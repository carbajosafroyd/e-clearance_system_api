"""
Student-facing notifications for over-the-counter payments.

Recording and cancelling a payment both move the student's balance while
the student is standing at a counter (or nowhere near one), so each
notification carries the whole story: which payment, what it paid for,
what the balance was before and after, and - for cancellations - why and
the reassurance that the full amount went back onto their balance. Sends
are best-effort - a notification failure must never undo or hide a
payment that already happened.
"""

from __future__ import annotations

import logging
import traceback

from api.notifications.service import notify_user

from .models import Payment

logger = logging.getLogger(__name__)

NOTIFICATION_SOURCE = "collection"
PAYMENT_RECEIVED_TYPE = "payment_received"
PAYMENT_CANCELLED_TYPE = "payment_cancelled"


def notify_payments_received(*, payments, actor) -> None:
    """Send each payment's student a receipt for the money just recorded.

    Takes the freshly created payments (one or a whole bulk batch) and
    re-reads them with their fees in one query - each fee's balance is
    already deducted, so the receipt shows before (previous_balance on the
    payment) and after (the fee's current balance).
    """
    try:
        rows = (
            Payment.objects.filter(pk__in=[p.pk for p in payments])
            .select_related("fee__category", "fee__student__user")
            .order_by("pk")
        )
        for payment in rows:
            fee = payment.fee
            recipient = fee.student.user
            if recipient is None:
                continue  # student has no portal account to notify

            category = fee.category.category_name
            scope = f"{fee.academic_year}, {fee.get_semester_display()}"
            amount = payment.amount_paid
            method = payment.get_payment_method_display()

            # Derived per payment, not read off the fee: in a bulk batch the
            # fee's stored balance is the FINAL figure after every item, but
            # each receipt must show what its own payment left behind.
            balance_after = payment.previous_balance - amount
            closing = (
                f"This fee is now fully paid — thank you for settling it!"
                if balance_after <= 0
                else (
                    f"Your remaining balance on this fee is "
                    f"₱{balance_after:,.2f}."
                )
            )

            message = (
                f"We received your {method} payment of ₱{amount:,.2f} for "
                f"{category} ({scope}). Thank you!\n"
                f"\n"
                f"Payment details:\n"
                f"• Payment No.: {payment.pk}\n"
                f"• Fee: {category} — {scope}\n"
                f"• Amount paid: ₱{amount:,.2f} ({method})\n"
                f"• Received on: {payment.created_at:%b %d, %Y}\n"
                f"• Balance before this payment: ₱{payment.previous_balance:,.2f}\n"
                f"• Balance after this payment: ₱{balance_after:,.2f}\n"
                f"\n"
                f"{closing} Keep this notification as your record of this "
                f"payment."
            )

            notify_user(
                user=recipient,
                sender=actor,
                title=f"Payment Received — ₱{amount:,.2f} for {category}",
                message=message,
                type=PAYMENT_RECEIVED_TYPE,
                source=NOTIFICATION_SOURCE,
                data={
                    "payment_id": payment.pk,
                    "fee_id": fee.pk,
                    "category": category,
                    "academic_year": fee.academic_year,
                    "semester": fee.semester,
                    "amount_paid": str(amount),
                    "payment_method": payment.payment_method,
                    "received_at": payment.created_at.isoformat()
                    if payment.created_at
                    else None,
                    "previous_balance": str(payment.previous_balance),
                    "new_balance": str(balance_after),
                    "fee_status": fee.status,
                },
            )
    except Exception:
        logger.error(
            "Failed to notify student(s) of received payment\n%s",
            traceback.format_exc(),
        )


def notify_payment_cancelled(*, payment, actor) -> None:
    """Tell the student everything about one cancelled OTC payment.

    `payment` must already be CANCELLED and its fee's balance already
    restored - the message reads the post-cancellation state off the rows.
    """
    try:
        fee = payment.fee
        recipient = fee.student.user
        if recipient is None:
            return  # student has no portal account to notify

        category = fee.category.category_name
        scope = f"{fee.academic_year}, {fee.get_semester_display()}"
        amount = payment.amount_paid
        method = payment.get_payment_method_display()

        message = (
            f"Your {method} payment of ₱{amount:,.2f} for {category} "
            f"({scope}) has been cancelled by the collection office.\n"
            f"\n"
            f"Cancelled payment details:\n"
            f"• Payment No.: {payment.pk}\n"
            f"• Fee: {category} — {scope}\n"
            f"• Amount: ₱{amount:,.2f} ({method})\n"
            f"• Originally received: {payment.created_at:%b %d, %Y}\n"
            f"• Cancelled on: {payment.cancelled_at:%b %d, %Y}\n"
            f"• Reason: {payment.cancellation_reason}\n"
            f"\n"
            f"What changed on your account:\n"
            f"• The ₱{amount:,.2f} this payment had settled was reversed.\n"
            f"• Your {category} fee balance is back to ₱{fee.balance:,.2f} "
            f"of ₱{fee.total_amount:,.2f} ({fee.get_status_display()}).\n"
            f"\n"
            f"Rest assured — the full ₱{amount:,.2f} from this cancelled "
            f"payment has been returned to your fee balance, so nothing is "
            f"lost from your account. If you already handed over this "
            f"amount or believe this cancellation was a mistake, please "
            f"visit the collection office and it will be sorted out for you."
        )

        notify_user(
            user=recipient,
            sender=actor,
            title=f"Payment Cancelled — ₱{amount:,.2f} Returned to Your Balance",
            message=message,
            type=PAYMENT_CANCELLED_TYPE,
            source=NOTIFICATION_SOURCE,
            data={
                "payment_id": payment.pk,
                "fee_id": fee.pk,
                "category": category,
                "academic_year": fee.academic_year,
                "semester": fee.semester,
                "amount_returned": str(amount),
                "payment_method": payment.payment_method,
                "paid_at": payment.created_at.isoformat()
                if payment.created_at
                else None,
                "cancelled_at": payment.cancelled_at.isoformat()
                if payment.cancelled_at
                else None,
                "cancellation_reason": payment.cancellation_reason,
                "restored_balance": str(fee.balance),
                "fee_total_amount": str(fee.total_amount),
                "fee_status": fee.status,
            },
        )
    except Exception:
        logger.error(
            "Failed to notify student of payment cancellation\n%s",
            traceback.format_exc(),
        )
