import base64
import io
import logging
import uuid
from typing import Any, Dict

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
import segno

from api.clearance.models import StudentClearance, StudentClearanceItem
from api.reports.services.pdf import render_pdf

logger = logging.getLogger(__name__)


def _format_year_level(lvl: int | None) -> str:
    if not lvl:
        return ""
    suffixes = {1: "1st Year", 2: "2nd Year", 3: "3rd Year", 4: "4th Year", 5: "5th Year"}
    return suffixes.get(lvl, f"Year {lvl}")


def generate_clearance_certificate_pdf(student_clearance: StudentClearance) -> bytes:
    """
    Renders an official clearance certificate PDF for a completed student clearance.
    Embeds a tamper-proof verification QR code generated via Segno.
    """
    if student_clearance.status != StudentClearance.ClearanceStatus.COMPLETED:
        raise PermissionDenied(
            "Clearance certificate is only available once all clearance requirements are completed."
        )

    if not student_clearance.verification_token:
        student_clearance.verification_token = uuid.uuid4()
        student_clearance.save(update_fields=["verification_token", "updated_at"])

    student = student_clearance.student
    program = getattr(student, "program", None)
    institute = getattr(program, "institute", None)
    school = getattr(institute, "school", None) if institute else None

    # Generate QR Code for digital verification
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000") or "http://localhost:3000"
    frontend_url = frontend_url.rstrip("/")
    verify_url = f"{frontend_url}/clearance/verify/{student_clearance.verification_token}"

    qr = segno.make(verify_url, error="h")
    qr_buffer = io.BytesIO()
    qr.save(qr_buffer, kind="png", scale=4)
    qr_base64 = base64.b64encode(qr_buffer.getvalue()).decode("ascii")
    qr_code_data_uri = f"data:image/png;base64,{qr_base64}"

    # Build items list
    items_data = []
    items = (
        student_clearance.items.select_related(
            "requirement",
            "requirement__authorizing_role",
            "cleared_by_user",
        )
        .prefetch_related("requirement__authorizing_roles")
        .order_by("requirement__order_sequence", "id")
    )

    for item in items:
        cleared_by = item.cleared_by_user
        cleared_by_name = ""
        if cleared_by:
            fname = getattr(cleared_by, "first_name", "") or ""
            lname = getattr(cleared_by, "last_name", "") or ""
            cleared_by_name = f"{fname} {lname}".strip() or getattr(cleared_by, "username", "")

        role_name = ""
        if item.requirement.authorizing_role:
            role_name = item.requirement.authorizing_role.name
        elif item.requirement.authorizing_roles.exists():
            role_name = ", ".join(r.name for r in item.requirement.authorizing_roles.all())

        cleared_at_formatted = ""
        if item.cleared_at:
            cleared_at_formatted = timezone.localtime(item.cleared_at).strftime("%b %d, %Y")

        items_data.append(
            {
                "order_sequence": item.requirement.order_sequence,
                "requirement_name": item.requirement.name,
                "authorizing_role_name": role_name or "Department Signatory",
                "cleared_by_name": cleared_by_name or "Authorized Signatory",
                "cleared_at_formatted": cleared_at_formatted,
            }
        )

    student_fname = getattr(student, "s_fname", "") or ""
    student_mname = getattr(student, "s_mname", "") or ""
    student_lname = getattr(student, "s_lname", "") or ""
    student_suffix = getattr(student, "s_suffix", "") or ""
    full_name_parts = [student_fname, student_mname, student_lname, student_suffix]
    full_name = " ".join(p.strip() for p in full_name_parts if p and p.strip())

    date_completed = student_clearance.date_completed or timezone.now()
    date_completed_formatted = timezone.localtime(date_completed).strftime("%B %d, %Y")
    generated_at_formatted = timezone.localtime(timezone.now()).strftime("%B %d, %Y %I:%M %p")

    context = {
        "school_name": getattr(school, "school_name", "Davao del Norte State College") or "Davao del Norte State College",
        "school_location": getattr(school, "location", "Panabo City, Davao del Norte") or "Panabo City, Davao del Norte",
        "institute_name": getattr(institute, "institute_name", "Institute of Computing") or "Institute of Computing",
        "clearance_type_name": student_clearance.clearance_type.name,
        "academic_year": student_clearance.clearance_type.academic_year,
        "semester": student_clearance.clearance_type.semester,
        "student_name": full_name or "Student",
        "student_number": getattr(student, "s_studentID", ""),
        "program_name": getattr(program, "name", "Bachelor of Science in Information Technology"),
        "year_level": _format_year_level(getattr(student, "s_lvl", None)),
        "date_completed_formatted": date_completed_formatted,
        "generated_at_formatted": generated_at_formatted,
        "verification_token": str(student_clearance.verification_token),
        "verify_url": verify_url,
        "qr_code_data_uri": qr_code_data_uri,
        "items": items_data,
    }

    return render_pdf(template_name="clearance/certificate.html", context=context)
