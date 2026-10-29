import logging
import threading
from django.conf import settings
from django.utils import timezone

from api.clearance.models import StudentClearance, StudentClearanceItem
from core.services.send_email_v2 import build_email, _execute_send
from core.services.email_branding import email_brand

logger = logging.getLogger(__name__)


def send_clearance_deficiency_email(item: StudentClearanceItem) -> None:
    """
    Sends an automated email alert when a deficiency is flagged or an item is rejected.
    Asynchronous and fail-safe.
    """
    try:
        student = getattr(item.student_clearance, "student", None)
        if not student:
            return

        recipient_email = getattr(student.user, "email", None) if getattr(student, "user", None) else None
        if not recipient_email:
            return

        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:5173") or "http://localhost:5173"
        action_url = f"{frontend_url.rstrip('/')}/clearance/items/{item.id}"

        student_name = f"{student.s_fname} {student.s_lname}".strip() or "Student"
        clearance_type_name = getattr(item.student_clearance.clearance_type, "name", "Clearance")
        requirement_name = item.requirement.name if item.requirement else "Requirement"

        context = {
            "student_name": student_name,
            "clearance_type_name": clearance_type_name,
            "requirement_name": requirement_name,
            "remarks": item.remarks,
            "amount_due": f"{item.amount_due:,.2f}" if item.amount_due else None,
            "action_url": action_url,
            **email_brand(getattr(student, "user", None)),
        }

        email = build_email(
            subject=f"Deficiency Notice: {requirement_name} ({clearance_type_name})",
            to_email=recipient_email,
            template_name="clearance_rejected",
            context=context,
        )

        if not getattr(settings, "EMAIL_SEND_ASYNC", True):
            _execute_send(email)
            return

        thread = threading.Thread(target=_execute_send, args=(email,))
        thread.start()
    except Exception as exc:
        logger.warning(
            "Failed to send deficiency email for item %s: %s",
            item.id,
            exc,
            exc_info=True,
        )


def send_clearance_completed_email(student_clearance: StudentClearance) -> None:
    """
    Sends an automated congratulatory email with attached PDF certificate
    when a student clearance is officially completed.
    Asynchronous and fail-safe.
    """
    try:
        student = getattr(student_clearance, "student", None)
        if not student:
            return

        recipient_email = getattr(student.user, "email", None) if getattr(student, "user", None) else None
        if not recipient_email:
            return

        from api.clearance.services.certificate_service import generate_clearance_certificate_pdf

        pdf_bytes = generate_clearance_certificate_pdf(student_clearance)

        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:5173") or "http://localhost:5173"
        verify_url = f"{frontend_url.rstrip('/')}/clearance/verify/{student_clearance.verification_token}"

        student_name = f"{student.s_fname} {student.s_lname}".strip() or "Student"
        clearance_type_name = getattr(student_clearance.clearance_type, "name", "Clearance")
        academic_year = getattr(student_clearance.clearance_type, "academic_year", "")
        semester = getattr(student_clearance.clearance_type, "semester", "")

        date_completed = student_clearance.date_completed or timezone.now()
        date_completed_formatted = timezone.localtime(date_completed).strftime("%B %d, %Y")

        context = {
            "student_name": student_name,
            "clearance_type_name": clearance_type_name,
            "academic_year": academic_year,
            "semester": semester,
            "date_completed_formatted": date_completed_formatted,
            "verify_url": verify_url,
            **email_brand(getattr(student, "user", None)),
        }

        email = build_email(
            subject=f"Official Clearance Certificate: {clearance_type_name}",
            to_email=recipient_email,
            template_name="clearance_completed",
            context=context,
        )

        filename = f"Clearance_Certificate_{getattr(student, 's_studentID', 'Completed')}.pdf"
        email.attach(filename, pdf_bytes, "application/pdf")

        if not getattr(settings, "EMAIL_SEND_ASYNC", True):
            _execute_send(email)
            return

        thread = threading.Thread(target=_execute_send, args=(email,))
        thread.start()
    except Exception as exc:
        logger.warning(
            "Failed to send clearance completed email for clearance %s: %s",
            student_clearance.id,
            exc,
            exc_info=True,
        )
