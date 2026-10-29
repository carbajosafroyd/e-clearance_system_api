"""
The optional-module reports: complaints, reports & ideas, the leaderboard,
point discounts and coupons - each in the same three shapes as the money
reports (JSON at the path, PDF at `<path>/pdf/`, Excel at `<path>/excel/`).

Two things differ from the money reports, and both are visible here rather
than hidden in the base:

* the permission is the module's own read grant, wrapped in `Feature(...)`
  so an institute that has switched the module off gets a refusal rather
  than an empty report that implies the module exists;
* the params serializer accepts `system` and `status` on top of the period
  and student-scope vocabulary.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from api.rbac import Feature
from core import features

from ..serializers.collection_report_serializers import ModuleReportParamsSerializer
from ..services.module_excel import (
    build_complaints_workbook,
    build_contributions_workbook,
    build_coupons_workbook,
    build_leaderboard_workbook,
    build_point_discounts_workbook,
)
from ..services.module_reports import (
    build_complaints_report,
    build_contributions_report,
    build_coupons_report,
    build_leaderboard_report,
    build_point_discounts_report,
)
from .base import (
    XLSX_CONTENT_TYPE,
    BaseReportExcelView,
    BaseReportJsonView,
    BaseReportPdfView,
)


class _ModuleReport:
    """
    What every module report view shares. Subclasses name the feature flag,
    the read permission and the builder; the three shapes come from the
    base classes unchanged.
    """

    feature: str = ""
    params_serializer_class = ModuleReportParamsSerializer
    builder = None

    @property
    def required_permissions(self) -> dict:
        needed = [self.view_permission]
        if self.export_permission:
            needed.append(self.export_permission)
        # `Feature` resolves the flag against the caller's institute, so the
        # same URL is on for one institute and refused for the next.
        return {"get": Feature(self.feature, needed)}

    def build_report(self, request, filters):
        return type(self).builder(user=request.user, filters=filters)


# --------------------------------------------------------------------------
# Complaints
# --------------------------------------------------------------------------


class _Complaints(_ModuleReport):
    feature = features.COMPLAINTS
    view_permission = "complaints.view_all_complaints"
    builder = staticmethod(build_complaints_report)


@extend_schema(tags=["Reports"])
class ComplaintsReportView(_Complaints, BaseReportJsonView):
    """Every complaint in the window, with the queue's totals by status, system and category."""

    @extend_schema(summary="Complaints report", parameters=[ModuleReportParamsSerializer],
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class ComplaintsReportPdfView(_Complaints, BaseReportPdfView):
    template_name = "reports/complaints.html"
    filename_prefix = "complaints"

    @extend_schema(summary="Complaints report (PDF)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class ComplaintsReportExcelView(_Complaints, BaseReportExcelView):
    workbook_builder = staticmethod(build_complaints_workbook)
    filename_prefix = "complaints"

    @extend_schema(summary="Complaints report (Excel)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)


# --------------------------------------------------------------------------
# Reports & ideas
# --------------------------------------------------------------------------


class _Contributions(_ModuleReport):
    feature = features.CONTRIBUTIONS
    view_permission = "rbac.view_contribution_report"
    builder = staticmethod(build_contributions_report)


@extend_schema(tags=["Reports"])
class ContributionsReportView(_Contributions, BaseReportJsonView):
    """Every report and idea filed in the window, with what it paid out."""

    @extend_schema(summary="Reports & ideas report", parameters=[ModuleReportParamsSerializer],
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class ContributionsReportPdfView(_Contributions, BaseReportPdfView):
    template_name = "reports/contributions.html"
    filename_prefix = "reports-and-ideas"

    @extend_schema(summary="Reports & ideas report (PDF)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class ContributionsReportExcelView(_Contributions, BaseReportExcelView):
    workbook_builder = staticmethod(build_contributions_workbook)
    filename_prefix = "reports-and-ideas"

    @extend_schema(summary="Reports & ideas report (Excel)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------


class _Leaderboard(_ModuleReport):
    feature = features.CONTRIBUTION_POINTS
    view_permission = "contributions.view_contribution_leaderboard"
    builder = staticmethod(build_leaderboard_report)


@extend_schema(tags=["Reports"])
class LeaderboardReportView(_Leaderboard, BaseReportJsonView):
    """The standing by points earned - the whole board, not a page of it."""

    @extend_schema(summary="Leaderboard report", parameters=[ModuleReportParamsSerializer],
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class LeaderboardReportPdfView(_Leaderboard, BaseReportPdfView):
    template_name = "reports/leaderboard.html"
    filename_prefix = "leaderboard"

    @extend_schema(summary="Leaderboard report (PDF)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class LeaderboardReportExcelView(_Leaderboard, BaseReportExcelView):
    workbook_builder = staticmethod(build_leaderboard_workbook)
    filename_prefix = "leaderboard"

    @extend_schema(summary="Leaderboard report (Excel)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)


# --------------------------------------------------------------------------
# Point discounts
# --------------------------------------------------------------------------


class _PointDiscounts(_ModuleReport):
    feature = features.CONTRIBUTION_POINTS
    view_permission = "contributions.view_feediscount"
    builder = staticmethod(build_point_discounts_report)


@extend_schema(tags=["Reports"])
class PointDiscountsReportView(_PointDiscounts, BaseReportJsonView):
    """Every points-funded discount requested in the window, and what the scheme cost."""

    @extend_schema(summary="Point discounts report", parameters=[ModuleReportParamsSerializer],
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class PointDiscountsReportPdfView(_PointDiscounts, BaseReportPdfView):
    template_name = "reports/point_discounts.html"
    filename_prefix = "point-discounts"

    @extend_schema(summary="Point discounts report (PDF)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class PointDiscountsReportExcelView(_PointDiscounts, BaseReportExcelView):
    workbook_builder = staticmethod(build_point_discounts_workbook)
    filename_prefix = "point-discounts"

    @extend_schema(summary="Point discounts report (Excel)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)


# --------------------------------------------------------------------------
# Coupons
# --------------------------------------------------------------------------


class _Coupons(_ModuleReport):
    feature = features.COUPONS
    view_permission = "coupons.view_couponbatch"
    builder = staticmethod(build_coupons_report)


@extend_schema(tags=["Reports"])
class CouponsReportView(_Coupons, BaseReportJsonView):
    """Every batch issued in the window, every code in them, and every redemption."""

    @extend_schema(summary="Coupons report", parameters=[ModuleReportParamsSerializer],
                   responses={200: OpenApiTypes.OBJECT})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class CouponsReportPdfView(_Coupons, BaseReportPdfView):
    template_name = "reports/coupons.html"
    filename_prefix = "coupons"

    @extend_schema(summary="Coupons report (PDF)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class CouponsReportExcelView(_Coupons, BaseReportExcelView):
    workbook_builder = staticmethod(build_coupons_workbook)
    filename_prefix = "coupons"

    @extend_schema(summary="Coupons report (Excel)", parameters=[ModuleReportParamsSerializer],
                   responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY})
    def get(self, request):
        return super().get(request)
