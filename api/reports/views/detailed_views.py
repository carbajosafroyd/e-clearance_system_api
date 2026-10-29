"""
The three detailed report endpoints, each in the same two forms as the
collection report: JSON at the path, PDF at `<path>/pdf/`.

All three require a period (see DetailedReportParamsSerializer) - they list
rows, not aggregates, and an unbounded listing is a database dump with a
page count.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from ..serializers import (
    DetailedFeesReportSerializer,
    DetailedReportParamsSerializer,
    ProgramSetReportSerializer,
    StaffReportSerializer,
)
from ..services import (
    build_detailed_fees_report,
    build_detailed_fees_workbook,
    build_program_set_report,
    build_program_set_workbook,
    build_staff_report,
    build_staff_workbook,
)
from .base import (
    XLSX_CONTENT_TYPE,
    BaseReportExcelView,
    BaseReportJsonView,
    BaseReportPdfView,
)


# --------------------------------------------------------------------------
# Detailed fees
# --------------------------------------------------------------------------


class _BuildsDetailedFees:
    # These three are the "detailed" family; the collection report keeps
    # the base's own permission.
    view_permission = "rbac.view_detailed_report"
    params_serializer_class = DetailedReportParamsSerializer

    def build_report(self, request, filters):
        return build_detailed_fees_report(user=request.user, filters=filters)


@extend_schema(tags=["Reports"])
class DetailedFeesReportView(_BuildsDetailedFees, BaseReportJsonView):
    """
    Every fee in the period, grouped by category, each with the submissions
    and payments behind it. A payment born from a submission appears under
    that submission; an over-the-counter payment under the fee - never both.
    """

    @extend_schema(
        summary="Detailed fees report",
        parameters=[DetailedReportParamsSerializer],
        responses=DetailedFeesReportSerializer,
    )
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class DetailedFeesReportPdfView(_BuildsDetailedFees, BaseReportPdfView):
    template_name = "reports/detailed_fees.html"
    filename_prefix = "detailed-fees"

    @extend_schema(
        summary="Detailed fees report (PDF)",
        parameters=[DetailedReportParamsSerializer],
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class DetailedFeesReportExcelView(_BuildsDetailedFees, BaseReportExcelView):
    """
    Flat sheets joined by their IDs, in reading order: Summary, Fees,
    Payments, Payment Submissions, Payment Submission Items - each with a
    totals row under its records.
    """

    workbook_builder = staticmethod(build_detailed_fees_workbook)
    filename_prefix = "detailed-fees"

    @extend_schema(
        summary="Detailed fees report (Excel)",
        parameters=[DetailedReportParamsSerializer],
        responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY},
    )
    def get(self, request):
        return super().get(request)


# --------------------------------------------------------------------------
# By staff
# --------------------------------------------------------------------------


class _BuildsStaffReport:
    # These three are the "detailed" family; the collection report keeps
    # the base's own permission.
    view_permission = "rbac.view_detailed_report"
    params_serializer_class = DetailedReportParamsSerializer

    def build_report(self, request, filters):
        return build_staff_report(user=request.user, filters=filters)


@extend_schema(tags=["Reports"])
class StaffReportView(_BuildsStaffReport, BaseReportJsonView):
    """Every payment each staff member received, grouped per member."""

    @extend_schema(
        summary="Collections by staff report",
        parameters=[DetailedReportParamsSerializer],
        responses=StaffReportSerializer,
    )
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class StaffReportPdfView(_BuildsStaffReport, BaseReportPdfView):
    template_name = "reports/by_staff.html"
    filename_prefix = "by-staff"

    @extend_schema(
        summary="Collections by staff report (PDF)",
        parameters=[DetailedReportParamsSerializer],
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class StaffReportExcelView(_BuildsStaffReport, BaseReportExcelView):
    workbook_builder = staticmethod(build_staff_workbook)
    filename_prefix = "by-staff"

    @extend_schema(
        summary="Collections by staff report (Excel)",
        parameters=[DetailedReportParamsSerializer],
        responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY},
    )
    def get(self, request):
        return super().get(request)


# --------------------------------------------------------------------------
# By program, year level and set
# --------------------------------------------------------------------------


class _BuildsProgramSetReport:
    # These three are the "detailed" family; the collection report keeps
    # the base's own permission.
    view_permission = "rbac.view_detailed_report"
    params_serializer_class = DetailedReportParamsSerializer

    def build_report(self, request, filters):
        return build_program_set_report(user=request.user, filters=filters)


@extend_schema(tags=["Reports"])
class ProgramSetReportView(_BuildsProgramSetReport, BaseReportJsonView):
    """
    Program -> year level -> set -> student -> fees, with the same totals
    shape at every level, each level summing exactly to its parent.
    """

    @extend_schema(
        summary="Collections by program, year level and set",
        parameters=[DetailedReportParamsSerializer],
        responses=ProgramSetReportSerializer,
    )
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class ProgramSetReportPdfView(_BuildsProgramSetReport, BaseReportPdfView):
    template_name = "reports/by_program_set.html"
    filename_prefix = "by-program-set"

    @extend_schema(
        summary="Collections by program, year level and set (PDF)",
        parameters=[DetailedReportParamsSerializer],
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class ProgramSetReportExcelView(_BuildsProgramSetReport, BaseReportExcelView):
    workbook_builder = staticmethod(build_program_set_workbook)
    filename_prefix = "by-program-set"

    @extend_schema(
        summary="Collections by program, year level and set (Excel)",
        parameters=[DetailedReportParamsSerializer],
        responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY},
    )
    def get(self, request):
        return super().get(request)
