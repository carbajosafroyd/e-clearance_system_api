"""
The two collection-report endpoints, on the shared report-view base.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema

from ..serializers import (
    CollectionReportParamsSerializer,
    CollectionReportSerializer,
)
from ..services import (
    build_collection_report,
    build_collection_report_workbook,
)
from ..services.pdf import collection_report_context
from .base import (
    XLSX_CONTENT_TYPE,
    BaseReportExcelView,
    BaseReportJsonView,
    BaseReportPdfView,
)


class _BuildsCollectionReport:
    def build_report(self, request, filters):
        return build_collection_report(user=request.user, filters=filters)


@extend_schema(tags=["Reports"])
class CollectionReportView(_BuildsCollectionReport, BaseReportJsonView):
    """
    The entire reports screen, in one request.

    One request rather than one per widget is the point: every figure on the
    page is cut from the same snapshot, so no two panels can disagree because
    a payment landed between two calls.
    """

    @extend_schema(
        summary="Collection report",
        parameters=[CollectionReportParamsSerializer],
        responses=CollectionReportSerializer,
    )
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class CollectionReportPdfView(_BuildsCollectionReport, BaseReportPdfView):
    """The same report, rendered for printing."""

    template_name = "reports/collection_report.html"
    filename_prefix = "collection-report"

    def pdf_context(self, **kwargs):
        # The collection template reads its sections as top-level names.
        return collection_report_context(**kwargs)

    @extend_schema(
        summary="Collection report (PDF)",
        description=(
            "Identical figures to the JSON endpoint - same parameters, same "
            "snapshot code - laid out by the `reports/collection_report.html` "
            "template."
        ),
        parameters=[CollectionReportParamsSerializer],
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    def get(self, request):
        return super().get(request)


@extend_schema(tags=["Reports"])
class CollectionReportExcelView(_BuildsCollectionReport, BaseReportExcelView):
    """The same report as an Excel workbook, one sheet per section."""

    workbook_builder = staticmethod(build_collection_report_workbook)
    filename_prefix = "collection-report"

    @extend_schema(
        summary="Collection report (Excel)",
        parameters=[CollectionReportParamsSerializer],
        responses={(200, XLSX_CONTENT_TYPE): OpenApiTypes.BINARY},
    )
    def get(self, request):
        return super().get(request)
