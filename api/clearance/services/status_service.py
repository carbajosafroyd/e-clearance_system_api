import logging
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from api.clearance.models import StudentClearance, StudentClearanceItem

logger = logging.getLogger(__name__)


def validate_sequential_clearance(item: StudentClearanceItem) -> None:
    """
    Ensures all requirements with lower order_sequence are CLEARED
    before this item can be marked as CLEARED.
    """
    if not item or not item.requirement or not item.student_clearance:
        return

    current_sequence = item.requirement.order_sequence

    preceding_uncleared = (
        StudentClearanceItem.objects.filter(
            student_clearance=item.student_clearance,
            requirement__order_sequence__lt=current_sequence,
        )
        .exclude(status=StudentClearanceItem.ItemStatus.CLEARED)
        .select_related("requirement")
        .order_by("requirement__order_sequence", "id")
    )

    if preceding_uncleared.exists():
        first_blocking = preceding_uncleared.first()
        raise ValidationError(
            {
                "detail": (
                    f"Sequential clearance locked: '{first_blocking.requirement.name}' "
                    f"(Step {first_blocking.requirement.order_sequence}) must be cleared first."
                )
            }
        )


@transaction.atomic
def evaluate_student_clearance_status(
    student_clearance: StudentClearance, actor=None
) -> StudentClearance:
    """
    Re-evaluates the clearance status of a student based on all associated items:
    - If all items are CLEARED -> COMPLETED (and sets date_completed)
    - If any item is HAS_ISSUE -> IN_PROGRESS (and resets date_completed)
    - If at least one item is CLEARED -> IN_PROGRESS (and resets date_completed)
    - If no items are CLEARED or HAS_ISSUE -> PENDING (and resets date_completed)
    Logs an audit action if status or date_completed changes.
    """
    items = list(student_clearance.items.all())
    total_items = len(items)

    if total_items == 0:
        return student_clearance

    cleared_count = sum(
        1 for item in items if item.status == StudentClearanceItem.ItemStatus.CLEARED
    )
    has_issues = any(
        item.status == StudentClearanceItem.ItemStatus.HAS_ISSUE for item in items
    )

    old_status = student_clearance.status
    old_date_completed = student_clearance.date_completed

    if cleared_count == total_items:
        new_status = StudentClearance.ClearanceStatus.COMPLETED
        new_date_completed = student_clearance.date_completed or timezone.now()
    elif has_issues or cleared_count > 0:
        new_status = StudentClearance.ClearanceStatus.IN_PROGRESS
        new_date_completed = None
    else:
        new_status = StudentClearance.ClearanceStatus.PENDING
        new_date_completed = None

    if new_status != old_status or new_date_completed != old_date_completed:
        student_clearance.status = new_status
        student_clearance.date_completed = new_date_completed
        update_fields = ["status", "date_completed", "updated_at"]

        if actor and hasattr(student_clearance, "updated_by"):
            student_clearance.updated_by = actor
            update_fields.append("updated_by")

        student_clearance.save(update_fields=update_fields)

        # Trigger notification if clearance transitioned to completed
        if (
            new_status == StudentClearance.ClearanceStatus.COMPLETED
            and old_status != StudentClearance.ClearanceStatus.COMPLETED
        ):
            from api.clearance.services.notification_service import notify_clearance_completed

            notify_clearance_completed(student_clearance, actor=actor)

        # Audit log integration
        try:
            from api.audits.models import AuditLog
            from api.audits.services import AuditLogService
            from api.clearance.permissions import is_clearance_admin, is_clearance_signatory

            actor_type = AuditLog.ActorTypeChoices.SYSTEM
            actor_id = None
            if actor:
                actor_id = getattr(actor, "id", None)
                if getattr(actor, "is_superuser", False) or is_clearance_admin(actor):
                    actor_type = AuditLog.ActorTypeChoices.ADMIN
                elif is_clearance_signatory(actor):
                    actor_type = AuditLog.ActorTypeChoices.STAFF
                elif hasattr(actor, "student"):
                    actor_type = AuditLog.ActorTypeChoices.STUDENT

            action = (
                AuditLog.Action.APPROVE
                if new_status == StudentClearance.ClearanceStatus.COMPLETED
                else AuditLog.Action.UPDATE
            )

            AuditLogService.log_action(
                system=AuditLog.System.CLEARANCE,
                entity_type="StudentClearance",
                entity_id=student_clearance.id,
                action=action,
                actor_type=actor_type,
                actor_id=actor_id,
                old_values={
                    "status": old_status,
                    "date_completed": str(old_date_completed) if old_date_completed else None,
                },
                new_values={
                    "status": new_status,
                    "date_completed": str(new_date_completed) if new_date_completed else None,
                },
            )
        except Exception as e:
            logger.warning(
                "Failed to write audit log for student clearance %s: %s",
                student_clearance.id,
                e,
            )

    return student_clearance
