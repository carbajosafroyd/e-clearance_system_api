"""
The audit-log report, in the project's usual three formats:

    GET audit-logs/report/          the snapshot as JSON
    GET audit-logs/report/pdf/      the same snapshot through the template
    GET audit-logs/report/excel/    the same snapshot as an .xlsx workbook

All three take exactly the filter parameters the audit-log LIST endpoint
takes (see AUDIT_FILTER_PARAMS) - same names, same multi-value syntax, same
validation - so a screen can flip between "browse the trail" and "export
the trail" without translating anything.

These are standalone APIViews rather than subclasses of the reports app's
BaseReportView because that base is welded to the collection-report filter
vocabulary (academic year / program / set); what IS shared is the machinery
underneath it: the PDF pipeline, the workbook theme, the export filename
convention and the binary renderers.
"""

import logging

from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.rbac import ActionPermission
from api.reports.renderers import PdfRenderer, XlsxRenderer
from api.reports.services import build_audit_logs_workbook, export_filename
from api.reports.services.pdf import render_pdf
from api.reports.views.base import XLSX_CONTENT_TYPE
from api.utils.renderer import CustomResponseRenderer

from .filters import parse_audit_filters
from .report import build_audit_log_report
from .report_serializers import AuditLogReportSerializer
from .views import AUDIT_FILTER_PARAMS

logger = logging.getLogger(__name__)


def _identity(request) -> dict:
    """Institute, school and requester - what every export prints in its header."""
    institute = getattr(request.user, "institute", None)
    school = getattr(institute, "school", None) if institute else None
    return {
        "institute_name": getattr(institute, "institute_name", None),
        "institute_acronym": getattr(institute, "acronym", None) or None,
        "school_name": getattr(school, "school_name", None),
        "prepared_by": request.user.get_username(),
    }


class BaseAuditLogReportView(APIView):
    # Same audience as the audit-log list: students must never see the trail.
    # The two export subclasses additionally require the export permission,
    # which they declare by overriding `required_permissions`.
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = {"get": "rbac.view_audit_report"}

    def snapshot(self, request) -> dict:
        filters = parse_audit_filters(request.query_params)
        logger.info(
            "Building audit-log report for user=%s filters=%s",
            request.user.username, filters,
        )
        return build_audit_log_report(filters)


@extend_schema(tags=["Audit Log"])
class AuditLogReportView(BaseAuditLogReportView):
    """The whole audit-report screen in one request - see the module docstring."""

    @extend_schema(
        summary="Audit log report",
        parameters=AUDIT_FILTER_PARAMS,
        responses=AuditLogReportSerializer,
        description=(
            "Aggregates plus the entries themselves for the filtered window: "
            "counts by action, system, actor type and entity type "
            "(zero-filled, so every enum value always appears), activity per "
            "day, the ten most active users, and the newest entries with the "
            "actor resolved to a username and full name. Aggregates always "
            "cover everything the filters matched; only `records` is capped "
            "- `summary.records_truncated` says when."
        ),
    )
    def get(self, request):
        return Response(self.snapshot(request))


@extend_schema(tags=["Audit Log"])
class AuditLogReportPdfView(BaseAuditLogReportView):
    """The same report, rendered for printing."""

    required_permissions = {"get": ["rbac.view_audit_report", "audits.export_audit_logs"]}

    # JSON first so validation errors come back in the usual envelope; the
    # PDF renderer makes `Accept: application/pdf` negotiable at all.
    renderer_classes = [CustomResponseRenderer, PdfRenderer]

    @extend_schema(
        summary="Audit log report (PDF)",
        parameters=AUDIT_FILTER_PARAMS,
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
        description=(
            "Identical figures to the JSON endpoint - same parameters, same "
            "snapshot code - laid out by the `audits/audit_logs_report.html` "
            "template. Old/new value diffs are not printed; use the Excel "
            "export or the retrieve endpoint for those."
        ),
    )
    def get(self, request):
        report = self.snapshot(request)

        pdf = render_pdf(
            template_name="audits/audit_logs_report.html",
            context={
                "report": report,
                # A real datetime, because the base template formats it with
                # the |date filter (which ignores plain strings).
                "generated_at": parse_datetime(report["generated_at"])
                or timezone.localtime(),
                **_identity(request),
            },
        )

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="'
            f'{export_filename(prefix="audit-logs", extension="pdf")}"'
        )
        return response


@extend_schema(tags=["Audit Log"])
class AuditLogReportExcelView(BaseAuditLogReportView):
    """The same report as an Excel workbook, one sheet per section."""

    required_permissions = {"get": ["rbac.view_audit_report", "audits.export_audit_logs"]}
    renderer_classes = [CustomResponseRenderer, XlsxRenderer]

    @extend_schema(
        summary="Audit log report (Excel)",
        parameters=AUDIT_FILTER_PARAMS,
        responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY},
        description=(
            "Sheets: Summary (filters and counts), Top Actors, Daily "
            "Activity, and Entries - the entry rows include the old/new "
            "value diffs as JSON strings."
        ),
    )
    def get(self, request):
        report = self.snapshot(request)

        workbook = build_audit_logs_workbook(report=report, **_identity(request))

        response = HttpResponse(workbook, content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = (
            f'attachment; filename="'
            f'{export_filename(prefix="audit-logs", extension="xlsx")}"'
        )
        return response
