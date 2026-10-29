from __future__ import annotations

from decimal import Decimal
import logging
import traceback
from typing import Any, Dict, Optional, List

from django.contrib.auth import get_user_model
from django.db import transaction

from api.notifications.models import Notification, NotificationBroadcast

from rest_framework.exceptions import ValidationError
from django.db.models import Count, Q

#
from django.db import transaction
from django.db.models import Q, Count, Sum
from rest_framework.exceptions import ValidationError

from api.notifications.models import Notification, NotificationBroadcast
from api.users.models import User

logger = logging.getLogger(__name__)

from dataclasses import dataclass, field

_BROADCAST_BATCH_SIZE = 500


def notify_user(
    *,
    user: User,
    title: str,
    message: str,
    type: str,
    source: str,
    sender: Optional[User] = None,
    data: Optional[dict[str, Any]] = None,
) -> Notification:
    """
    Create and persist a single notification for one user.

    The single entry point other apps should call whenever exactly one
    user needs to be notified about something (a payment approved, a
    collection reminder, a one-off system message, etc).

    Args:
        user: The recipient of the notification.
        title: Short headline shown in the slim list view.
        message: Full body text shown in the detail view.
        type: One of `Notification.NotificationType` choices.
        source: Identifies which subsystem raised the notification
            (e.g. "finance", "enrollment", "collections", "system").
        sender: Optional user who triggered the notification (e.g. the
            staff member who approved a payment). None for purely
            system-generated notifications.
        data: Optional JSON-serializable payload with extra context the
            frontend can use to render type-specific detail, e.g.
            {"payment_id": 42, "amount": "1000.00"}.

    Returns:
        The created Notification instance.
    """
    notification = Notification.objects.create(
        user=user,
        title=title,
        message=message,
        type=type,
        source=source,
        sender=sender,
        data=data,
    )

    logger.debug(
        "notification.created notification_id=%s user_id=%s type=%s source=%s",
        notification.pk,
        getattr(user, "pk", None),
        type,
        source,
    )
    return notification


def mark_as_read(notification: Notification) -> Notification:
    """
    Mark a single notification as read, idempotently.

    No-ops — and doesn't touch the database — if the notification is
    already read, so it's safe to call unconditionally (e.g. from the
    retrieve endpoint on every GET, whether or not it was already read).

    Args:
        notification: The Notification instance to mark as read.

    Returns:
        The same Notification instance, guaranteed is_read=True.
    """
    if notification.is_read:
        return notification

    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return notification


def mark_all_as_read(user: User) -> int:
    return Notification.objects.filter(user=user, is_read=False).update(is_read=True)


def get_unread_count(user: User) -> int:
    """
    Return the number of unread notifications for a given user.

    Backs the `/notifications/unread-count/` endpoint, which the
    frontend polls for a bell-icon badge.

    Args:
        user: The user to count unread notifications for.

    Returns:
        Count of Notification rows where user=user and is_read=False.
    """
    return Notification.objects.filter(user=user, is_read=False).count()


def create_targeted_broadcast(
    *,
    sender: User,
    title: str,
    message: str,
    notification_type: str,
    source: str = "collection",
    institute=None,
    program_id: Optional[int] = None,
    year_level: Optional[int] = None,
    user_ids: Optional[List[int]] = None,
    exclude_sender: bool = True,
    all_users: bool = False,
) -> NotificationBroadcast:
    """
    Unified notification engine handling system-wide broadcasts (Superadmin),
    granularly targeted institutional alerts (Staff), and specific user lists.
    """
    if all_users:
        if not sender.is_superuser:
            raise ValidationError("Only Super Admins can broadcast to all users.")
        if user_ids:
            raise ValidationError(
                "Cannot specify user_ids when broadcasting to all users."
            )

        user_filters = Q(is_active=True)
    else:
        # Base filter: Only active students
        user_filters = Q(student__isnull=False, is_active=True)

        # Enforce multi-tenancy limits for normal admin/staff
        if not sender.is_superuser:
            if not sender.institute:
                raise ValidationError(
                    "Institutional staff must belong to an institute to send notifications."
                )
            user_filters &= Q(student__program__institute=sender.institute)
        else:
            # If a superadmin explicitly requested an institute branch search
            if institute and hasattr(sender, "institute") and sender.institute:
                user_filters &= Q(student__program__institute=sender.institute)

        if program_id:
            user_filters &= Q(student__program_id=program_id)
        if year_level:
            user_filters &= Q(student__year_level=year_level)

        if user_ids:
            user_filters &= Q(pk__in=user_ids)

    if exclude_sender:
        user_filters &= ~Q(pk=sender.pk)

    recipients = User.objects.filter(user_filters)

    if not recipients.exists():
        raise ValidationError(
            "No users found matching the selected broadcast criteria."
        )

    with transaction.atomic():
        broadcast = NotificationBroadcast.objects.create(
            sender=sender,
            title=title,
            message=message,
            target_institute=institute if not all_users else False,
            target_program_id=program_id,
            target_year_level=year_level,
            total_recipients=recipients.count(),
            target_all_users=all_users,
        )

        notifications_to_create = [
            Notification(
                user=recipient,
                sender=sender,
                source=source,
                title=title,
                message=message,
                type=notification_type,
                data={"broadcast_id": broadcast.id},  # type: ignore
                broadcast=broadcast,
            )
            for recipient in recipients
        ]

        # Single SQL insert batch operation execution
        Notification.objects.bulk_create(notifications_to_create, batch_size=500)

    logger.info(
        "notification.broadcast successful. global=%s targeted_ids=%s sender=%s total_recipients=%s",
        all_users,
        bool(user_ids),
        sender.username,
        broadcast.total_recipients,
    )

    return broadcast


@dataclass(frozen=True)
class BulkNotifyRecipient:
    """One recipient + the data unique to their notification.
    e.g. their own fee_id/amount/due_date, so the frontend can render
    the right details when they open the notification."""

    user_id: int
    data: Dict[str, Any] = field(default_factory=dict)
    title: Optional[str] = None  # overrides the broadcast-level title, if set
    message: Optional[str] = None  # overrides the broadcast-level message, if set


def create_bulk_notify(
    *,
    sender: User,
    title: str,
    message: str,
    notification_type: str,
    recipients: List[BulkNotifyRecipient],
    source: str = "collection",
    exclude_sender: bool = True,
) -> NotificationBroadcast:
    """
    Sibling to create_targeted_broadcast, for the case where every recipient
    needs their OWN data payload instead of one identical notification blasted
    to a filtered queryset. Use this for things like "fee issued" where each
    student's fee_id/amount/due_date differs.
    """
    if not recipients:
        raise ValidationError("recipients cannot be empty.")

    recipient_ids = [r.user_id for r in recipients]
    if len(recipient_ids) != len(set(recipient_ids)):
        raise ValidationError("Duplicate user_id in recipients.")

    if exclude_sender:
        recipients = [r for r in recipients if r.user_id != sender.pk]
        recipient_ids = [r.user_id for r in recipients]

    if not recipients:
        raise ValidationError("No recipients left after excluding sender.")

    # Validate every id resolves to an active user and, for non-superusers,
    # actually belongs to the sender's institute. Without this, a caller could
    # pass any user_id list and notify people outside the sender's tenancy.
    user_filters = Q(pk__in=recipient_ids, is_active=True)
    if not sender.is_superuser:
        if not sender.institute:
            raise ValidationError(
                "Institutional staff must belong to an institute to send notifications."
            )
        user_filters &= Q(student__program__institute=sender.institute)

    valid_ids = set(User.objects.filter(user_filters).values_list("pk", flat=True))
    missing_ids = set(recipient_ids) - valid_ids
    if missing_ids:
        raise ValidationError(
            f"These user_ids are invalid, inactive, or outside your institute: {sorted(missing_ids)}"
        )

    with transaction.atomic():
        broadcast = NotificationBroadcast.objects.create(
            sender=sender,
            title=title,
            message=message,
            total_recipients=len(recipients),
        )
        notifications_to_create = [
            Notification(
                user_id=r.user_id,
                sender=sender,
                source=source,
                title=r.title or title,
                message=r.message or message,
                type=notification_type,
                data={"broadcast_id": broadcast.id, **r.data},  # type: ignore
                broadcast=broadcast,
            )
            for r in recipients
        ]
        Notification.objects.bulk_create(notifications_to_create, batch_size=500)

    logger.info(
        "notification.bulk_notify successful. sender=%s total_recipients=%s",
        sender.username,
        broadcast.total_recipients,
    )
    return broadcast

