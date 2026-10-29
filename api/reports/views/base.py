"""
The two view shapes every report is served through.

A report is defined by three things: which params serializer validates its
query string, which builder turns filters into a snapshot, and - for the PDF
- which template lays it out. Subclasses supply those; everything else
(permissions, parsing, institute scope, the attachment header) lives here
once. Adding a report means two small subclasses and two lines of routing.
"""

from __future__ import annotations

from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.rbac import ActionPermission
from api.utils.renderer import CustomResponseRenderer

from ..periods import ReportFilters
from ..renderers import PdfRenderer, XlsxRenderer
from ..serializers import CollectionReportParamsSerializer
from ..services.common import export_filename
from ..services.pdf import render_pdf, report_context

XLSX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


class BaseReportView(APIView):
    # A student holding a valid token must not be able to read institute-wide
    # totals.
    #
    # Two knobs instead of a hard-coded table, because every report exists in
    # three shapes and each shape is a subclass: `view_permission` says which
    # report family this is, `export_permission` is added by the PDF/Excel
    # shapes. Building the table in a property keeps both correct however the
    # subclass mixes them - a plain class attribute on a report mixin would
    # shadow the base's and silently drop the export requirement.
    permission_classes = [IsAuthenticated, ActionPermission]

    #: Gate for reading this report. Override on the per-report mixin.
    view_permission = "rbac.view_collection_report"

    #: Extra gate for the shapes that hand back a file.
    export_permission: str | None = None

    params_serializer_class = CollectionReportParamsSerializer

    @property
    def required_permissions(self) -> dict:
        needed = [self.view_permission]
        if self.export_permission:
            needed.append(self.export_permission)
        return {"get": needed}

    def build_report(self, request, filters: ReportFilters) -> dict:
        raise NotImplementedError

    def snapshot(self, request) -> tuple[dict, ReportFilters]:
        params = self.params_serializer_class(data=request.query_params)
        params.is_valid(raise_exception=True)

        filters = ReportFilters.from_validated_data(params.validated_data)
        return self.build_report(request, filters), filters

    @staticmethod
    def identity(request) -> dict:
        """
        Who this file belongs to: institute, its school, and who asked.

        Every export prints these - a workbook forwarded up the chain, or a
        PDF found in a drawer, has to say where it came from without the
        system to hand. Names only; the logo would mean a network fetch
        mid-render for every export.
        """
        institute = getattr(request.user, "institute", None)
        school = getattr(institute, "school", None) if institute else None
        return {
            "institute_name": getattr(institute, "institute_name", None),
            "institute_acronym": getattr(institute, "acronym", None) or None,
            "school_name": getattr(school, "school_name", None),
            "prepared_by": request.user.get_username(),
        }


class BaseReportJsonView(BaseReportView):
    """`GET <path>/` - the snapshot as JSON, in the project envelope."""

    def get(self, request):
        report, _ = self.snapshot(request)
        return Response(report)


class BaseReportPdfView(BaseReportView):
    """`GET <path>/pdf/` - the same snapshot through the report's template."""

    export_permission = "rbac.export_report"

    # JSON first so validation errors come back in the usual envelope; the PDF
    # renderer is here so `Accept: application/pdf` is negotiable at all.
    renderer_classes = [CustomResponseRenderer, PdfRenderer]

    template_name = ""
    filename_prefix = "report"

    def pdf_context(self, **kwargs) -> dict:
        """Override to hand the template extra names beyond the shared base."""
        return report_context(**kwargs)

    def get(self, request):
        report, filters = self.snapshot(request)

        pdf = render_pdf(
            template_name=self.template_name,
            context=self.pdf_context(
                report=report,
                filters=filters,
                **self.identity(request),
            ),
        )

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="'
            f'{export_filename(prefix=self.filename_prefix, extension="pdf")}"'
        )
        return response


class BaseReportExcelView(BaseReportView):
    """`GET <path>/excel/` - the same snapshot as an .xlsx workbook."""

    export_permission = "rbac.export_report"

    renderer_classes = [CustomResponseRenderer, XlsxRenderer]

    # A callable (report, filters, institute_name, prepared_by) -> bytes;
    # subclasses assign one of the builders from services/excel.py. Wrapped
    # in staticmethod() so Python does not try to bind it to the view.
    workbook_builder = None

    filename_prefix = "report"

    def get(self, request):
        report, filters = self.snapshot(request)

        workbook = type(self).workbook_builder(
            report=report,
            filters=filters,
            **self.identity(request),
        )

        response = HttpResponse(workbook, content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = (
            f'attachment; filename="'
            f'{export_filename(prefix=self.filename_prefix, extension="xlsx")}"'
        )
        return response
