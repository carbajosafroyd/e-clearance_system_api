"""
Notification fan-out for fee generation.

Both flows notify the same three audiences in the same order - the operator
when the job is accepted, every billed student once the fees land, then the
operator again with the summary - so the copy lives here once and takes its
wording from the strategy's label.

Every send is best-effort: a notification backend hiccup must never roll back
or hide fees that were successfully created.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from api.notifications.service import (
    BulkNotifyRecipient,
    create_bulk_notify,
    notify_user,
)
from api.students.models import Student
from api.users.models import User

from ...constants import NOTIFICATION_SOURCE, FeeNotificationType
from .strategy import FeeGenerationStrategy
from .types import GenerationOutcome, GenerationRequest

logger = logging.getLogger(__name__)


def _safe(send: Callable[..., Any], **kwargs: Any) -> None:
    try:
        send(**kwargs)
    except Exception:
        logger.error(
            "notification send failed (%s)\n%s",
            getattr(send, "__name__", send),
            traceback.format_exc(),
        )


def notify_queued(
    *, actor: User, label: str, academic_year: str, semester: str
) -> None:
    """Confirm to the operator that the job was accepted.

    Called from the request thread *before* the worker starts - otherwise a
    small institute finishes generating first and the operator sees "complete"
    arrive ahead of "started".
    """
    _safe(
        notify_user,
        user=actor,
        sender=actor,
        title=f"{label} Fee Generation Started",
        message=(
            f"Your request to generate {label} fees for "
            f"{academic_year} - {semester} has been received and is now "
            f"processing."
        ),
        type=FeeNotificationType.GENERATION_STARTED,
        source=NOTIFICATION_SOURCE,
    )


def notify_students(
    *,
    actor: User,
    strategy: FeeGenerationStrategy,
    request: GenerationRequest,
    outcome: GenerationOutcome,
) -> None:
    if not outcome.added:
        return

    student_ids = [row["student_id"] for row in outcome.added]
    # Student.user is a OneToOne, so this is at most one recipient per fee and
    # create_bulk_notify's duplicate-user guard cannot trip.
    user_id_by_student = dict(
        Student.objects.filter(id__in=student_ids).values_list("id", "user_id")
    )

    scope = f"{strategy.academic_year} - {strategy.semester}"
    recipients: list[BulkNotifyRecipient] = []

    for row in outcome.added:
        user_id = user_id_by_student.get(row["student_id"])
        if user_id is None:
            continue  # student has no portal account yet

        recipients.append(
            BulkNotifyRecipient(
                user_id=user_id,
                data={
                    "fee_id": row["fee_id"],
                    "amount": str(row["amount"]),
                    "due_date": request.due_date.isoformat(),
                    "category": strategy.label,
                    "academic_year": strategy.academic_year,
                    "semester": strategy.semester,
                    "waived": row["waived"],
                },
                message=(
                    f"You've been exempted from the new {strategy.label} fee "
                    f"for {scope}."
                    if row["waived"]
                    else (
                        f"A new {strategy.label} fee of "
                        f"₱{row['amount']:,.2f} has been issued for "
                        f"{scope}, due {request.due_date:%b %d, %Y}."
                    )
                ),
            )
        )

    if not recipients:
        return

    _safe(
        create_bulk_notify,
        sender=actor,
        title=f"New {strategy.label} Fee Issued",
        message=(
            f"A new {strategy.label} fee has been issued for {scope}. "
            f"Please check your fees for details."
        ),
        notification_type=FeeNotificationType.FEE_ISSUED,
        source=NOTIFICATION_SOURCE,
        recipients=recipients,
        exclude_sender=True,
    )


def notify_complete(
    *,
    actor: User,
    strategy: FeeGenerationStrategy,
    outcome: GenerationOutcome,
) -> None:
    _safe(
        notify_user,
        user=actor,
        title=f"{strategy.label} Fee Generation Complete",
        message=(
            f"Your fee generation request for [{strategy.label.upper()}] | "
            f"{strategy.academic_year} - {strategy.semester} has finished. "
            f"{len(outcome.added)} fee(s) created "
            f"({outcome.waived_count} waived), {len(outcome.not_added)} not added."
        ),
        type=FeeNotificationType.GENERATION_COMPLETE,
        source=NOTIFICATION_SOURCE,
        data=outcome.as_notification_payload(),
    )


def notify_failed(*, actor: User, label: str, error: str) -> None:
    _safe(
        notify_user,
        user=actor,
        title=f"{label} Fee Generation Failed",
        message=(
            "Something went wrong while generating fees. Please try again or "
            "contact support."
        ),
        type=FeeNotificationType.GENERATION_FAILED,
        source=NOTIFICATION_SOURCE,
        # Short reason only - the full traceback goes to the log, not into a
        # payload the frontend renders.
        data={"error": error},
    )
