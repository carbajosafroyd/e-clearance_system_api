"""
The three ways a complaint ends, and the notification each one sends.

Closing a complaint without telling the student is the failure mode this
module exists to prevent, so the notification is not the caller's job: it
happens here, in the same call that changes the status, for every path
including a rejection. A student who hears nothing assumes nothing happened.

Everything is `select_for_update`d and re-checks the status inside the lock,
so two officers answering at once serialise instead of both writing an
answer.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from api.notifications import service as notifications
from api.notifications.models import Notification

from .models import Complaint

logger = logging.getLogger(__name__)


def _locked(complaint: Complaint) -> Complaint:
    return (
        Complaint.objects.select_for_update()
        .select_related("student__user", "student__program")
        .get(pk=complaint.pk)
    )


def _require_open(complaint: Complaint) -> None:
    if not complaint.is_open:
        raise serializers.ValidationError(
            {"detail": f"This complaint is already {complaint.get_status_display().lower()}."}
        )


def _notify(complaint: Complaint, *, title: str, message: str, type: str, sender):
    """Tell the student what happened, if their account still exists.

    A complaint outlives the account that filed it (a graduate, a transfer),
    and a missing user is not a reason to fail the officer's action - so this
    logs and moves on rather than raising.
    """
    user = getattr(complaint.student, "user", None)
    if user is None:
        logger.info(
            "complaint.notify_skipped complaint_id=%s reason=no_user", complaint.pk
        )
        return

    notifications.notify_user(
        user=user,
        title=title,
        message=message,
        type=type,
        source=Notification.NotificationSource.COMPLAINTS,
        sender=sender,
        data={
            "complaint_id": complaint.pk,
            "status": complaint.status,
            "system": complaint.system,
            "category": complaint.category,
            "related_fee_id": complaint.related_fee_id,
        },
    )


@transaction.atomic
def acknowledge_complaint(*, complaint: Complaint, actor) -> Complaint:
    """Tell the student somebody has picked it up. Optional, and not an answer."""
    locked = _locked(complaint)
    _require_open(locked)

    if locked.status == Complaint.Status.ACKNOWLEDGED:
        return locked

    locked.status = Complaint.Status.ACKNOWLEDGED
    locked.handled_by = actor
    locked.updated_by = actor
    locked.save(update_fields=["status", "handled_by", "updated_by", "updated_at"])

    _notify(
        locked,
        title="Somebody is looking at your complaint",
        message=(
            f'Your complaint "{locked.title}" has been picked up by the '
            f"{locked.get_system_display()} desk. You will hear back when it "
            "is settled."
        ),
        type=Notification.NotificationType.COMPLAINT_ACKNOWLEDGED,
        sender=actor,
    )
    return locked


@transaction.atomic
def resolve_complaint(*, complaint: Complaint, actor, response: str) -> Complaint:
    """Close it as handled, and tell the student what was done."""
    locked = _locked(complaint)
    _require_open(locked)

    response = (response or "").strip()
    if not response:
        raise serializers.ValidationError(
            {"response": "Say what was done. The student is shown this."}
        )

    locked.status = Complaint.Status.RESOLVED
    locked.response = response
    locked.handled_by = actor
    locked.handled_at = timezone.now()
    locked.updated_by = actor
    locked.save()

    _notify(
        locked,
        title="Your complaint has been handled",
        message=f'"{locked.title}"\n\n{response}',
        type=Notification.NotificationType.COMPLAINT_RESOLVED,
        sender=actor,
    )
    return locked


@transaction.atomic
def reject_complaint(*, complaint: Complaint, actor, response: str) -> Complaint:
    """Close it without action - which still owes the student a reason."""
    locked = _locked(complaint)
    _require_open(locked)

    response = (response or "").strip()
    if not response:
        raise serializers.ValidationError(
            {"response": "Give a reason. The student is shown this."}
        )

    locked.status = Complaint.Status.REJECTED
    locked.response = response
    locked.handled_by = actor
    locked.handled_at = timezone.now()
    locked.updated_by = actor
    locked.save()

    _notify(
        locked,
        title="Your complaint was closed",
        message=f'"{locked.title}"\n\n{response}',
        type=Notification.NotificationType.COMPLAINT_REJECTED,
        sender=actor,
    )
    return locked


@transaction.atomic
def withdraw_complaint(*, complaint: Complaint, actor, reason: str = "") -> Complaint:
    """The student's own way out, while nobody has answered yet.

    No notification: they are the one doing it.
    """
    locked = _locked(complaint)
    _require_open(locked)

    locked.status = Complaint.Status.WITHDRAWN
    locked.response = (reason or "").strip() or None
    locked.handled_at = timezone.now()
    locked.updated_by = actor
    locked.save()
    return locked
