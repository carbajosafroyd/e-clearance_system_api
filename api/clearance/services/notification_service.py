import logging
from typing import Optional

from django.contrib.auth import get_user_model
from django.db.models import Q

from api.clearance.models import StudentClearance, StudentClearanceItem
from api.notifications.models import Notification
from api.notifications.service import notify_user

logger = logging.getLogger(__name__)
User = get_user_model()


def get_signatories_for_requirement(requirement, institute_id=None, school_id=None):
    """
    Returns active users who belong to any of the authorizing groups for the requirement,
    optionally scoped to the given institute or school.
    """
    if not requirement:
        return User.objects.none()

    role_ids = []
    if requirement.authorizing_role_id:
        role_ids.append(requirement.authorizing_role_id)
    role_ids.extend(requirement.authorizing_roles.values_list("id", flat=True))

    if not role_ids:
        return User.objects.none()

    qs = User.objects.filter(groups__id__in=role_ids, is_active=True)
    if institute_id:
        qs = qs.filter(Q(institute_id=institute_id) | Q(is_superuser=True))
    elif school_id:
        qs = qs.filter(Q(school_id=school_id) | Q(is_superuser=True))

    return qs.distinct()


def notify_deficiency_issue(item: StudentClearanceItem, actor=None) -> None:
    """Notify the student that an issue / deficiency was flagged on a requirement."""
    try:
        student = getattr(item.student_clearance, "student", None)
        if not student or not getattr(student, "user", None):
            return

        remarks_str = item.remarks or "Please contact the authorizing office for details."
        amount_str = f" Amount Due: ₱{item.amount_due:,.2f}" if item.amount_due else ""
        body = f"A deficiency was flagged for '{item.requirement.name}'. Remarks: {remarks_str}{amount_str}"

        notify_user(
            user=student.user,
            title=f"Clearance Deficiency Noted: {item.requirement.name}",
            message=body,
            type=Notification.NotificationType.CLEARANCE_ISSUE,
            source=Notification.NotificationSource.CLEARANCE,
            sender=actor,
            data={
                "student_clearance_item_id": item.id,
                "student_clearance_id": item.student_clearance_id,
                "requirement_id": item.requirement_id,
                "amount_due": str(item.amount_due) if item.amount_due is not None else None,
                "remarks": item.remarks,
            },
        )

        try:
            from api.clearance.services.email_service import send_clearance_deficiency_email
            send_clearance_deficiency_email(item)
        except Exception as email_err:
            logger.warning("Failed to dispatch deficiency email for item %s: %s", item.id, email_err)
    except Exception as e:
        logger.warning("Failed to send deficiency notification for item %s: %s", item.id, e)


def notify_proof_submitted(item: StudentClearanceItem, student_user=None) -> None:
    """Notify signatories that a student uploaded proof of compliance."""
    try:
        student = getattr(item.student_clearance, "student", None)
        institute_id = (
            getattr(student.program, "institute_id", None)
            if student and getattr(student, "program", None)
            else None
        )

        signatories = get_signatories_for_requirement(
            item.requirement, institute_id=institute_id
        )

        student_name = f"{student.s_fname} {student.s_lname}" if student else "A student"
        student_id_str = getattr(student, "s_studentID", "")
        proof_note = item.proof_notes or "None provided"

        for signatory in signatories:
            notify_user(
                user=signatory,
                title=f"Proof Uploaded: {item.requirement.name}",
                message=(
                    f"Student {student_name} ({student_id_str}) uploaded proof of compliance "
                    f"for '{item.requirement.name}'. Notes: {proof_note}"
                ),
                type=Notification.NotificationType.CLEARANCE_PROOF_SUBMITTED,
                source=Notification.NotificationSource.CLEARANCE,
                sender=student_user,
                data={
                    "student_clearance_item_id": item.id,
                    "student_clearance_id": item.student_clearance_id,
                    "requirement_id": item.requirement_id,
                    "proof_notes": item.proof_notes,
                },
            )
    except Exception as e:
        logger.warning("Failed to send proof submitted notification for item %s: %s", item.id, e)


def notify_item_cleared(item: StudentClearanceItem, actor=None) -> None:
    """Notify the student that a requirement has been cleared."""
    try:
        student = getattr(item.student_clearance, "student", None)
        if not student or not getattr(student, "user", None):
            return

        notify_user(
            user=student.user,
            title=f"Requirement Cleared: {item.requirement.name}",
            message=f"Your requirement '{item.requirement.name}' has been cleared.",
            type=Notification.NotificationType.CLEARANCE_CLEARED,
            source=Notification.NotificationSource.CLEARANCE,
            sender=actor,
            data={
                "student_clearance_item_id": item.id,
                "student_clearance_id": item.student_clearance_id,
                "requirement_id": item.requirement_id,
            },
        )
    except Exception as e:
        logger.warning("Failed to send item cleared notification for item %s: %s", item.id, e)


def notify_clearance_completed(student_clearance: StudentClearance, actor=None) -> None:
    """Notify the student that their clearance is fully completed."""
    try:
        student = getattr(student_clearance, "student", None)
        if not student or not getattr(student, "user", None):
            return

        type_name = getattr(student_clearance.clearance_type, "name", "Semestral")

        notify_user(
            user=student.user,
            title="Clearance Completed!",
            message=f"Congratulations! Your {type_name} clearance is officially completed.",
            type=Notification.NotificationType.CLEARANCE_COMPLETED,
            source=Notification.NotificationSource.CLEARANCE,
            sender=actor,
            data={
                "student_clearance_id": student_clearance.id,
                "verification_token": (
                    str(student_clearance.verification_token)
                    if student_clearance.verification_token
                    else None
                ),
            },
        )

        try:
            from api.clearance.services.email_service import send_clearance_completed_email
            send_clearance_completed_email(student_clearance)
        except Exception as email_err:
            logger.warning(
                "Failed to dispatch completion email for clearance %s: %s",
                student_clearance.id,
                email_err,
            )
    except Exception as e:
        logger.warning(
            "Failed to send clearance completed notification for clearance %s: %s",
            student_clearance.id,
            e,
        )
