"""
Report routes.

Every report follows one pattern: JSON at the path, PDF at `<path>/pdf/`,
Excel at `<path>/excel/`. Registered *with* the trailing slash on purpose -
the frontend calls these directly, and a path without the slash would make
every call eat an APPEND_SLASH 301 first.

These live under the `summaries/` prefix, which the router also owns. Django
resolves in order, and `api/urls.py` includes this module before the router,
so these paths win. Any other `summaries/...` URL still reaches
`SummaryViewSets` as before.
"""

from django.urls import path

from core import features

from .views import (
    CollectionReportExcelView,
    CollectionReportPdfView,
    CollectionReportView,
    DetailedFeesReportExcelView,
    DetailedFeesReportPdfView,
    DetailedFeesReportView,
    ProgramSetReportExcelView,
    ProgramSetReportPdfView,
    ProgramSetReportView,
    StaffReportExcelView,
    StaffReportPdfView,
    StaffReportView,
)

urlpatterns = [
    path(
        "summaries/collection-report/",
        CollectionReportView.as_view(),
        name="collection-report",
    ),
    path(
        "summaries/collection-report/pdf/",
        CollectionReportPdfView.as_view(),
        name="collection-report-pdf",
    ),
    path(
        "summaries/collection-report/excel/",
        CollectionReportExcelView.as_view(),
        name="collection-report-excel",
    ),
    path(
        "summaries/detailed-fees/",
        DetailedFeesReportView.as_view(),
        name="detailed-fees",
    ),
    path(
        "summaries/detailed-fees/pdf/",
        DetailedFeesReportPdfView.as_view(),
        name="detailed-fees-pdf",
    ),
    path(
        "summaries/detailed-fees/excel/",
        DetailedFeesReportExcelView.as_view(),
        name="detailed-fees-excel",
    ),
    path(
        "summaries/by-staff/",
        StaffReportView.as_view(),
        name="by-staff",
    ),
    path(
        "summaries/by-staff/pdf/",
        StaffReportPdfView.as_view(),
        name="by-staff-pdf",
    ),
    path(
        "summaries/by-staff/excel/",
        StaffReportExcelView.as_view(),
        name="by-staff-excel",
    ),
    path(
        "summaries/by-program-set/",
        ProgramSetReportView.as_view(),
        name="by-program-set",
    ),
    path(
        "summaries/by-program-set/pdf/",
        ProgramSetReportPdfView.as_view(),
        name="by-program-set-pdf",
    ),
    path(
        "summaries/by-program-set/excel/",
        ProgramSetReportExcelView.as_view(),
        name="by-program-set-excel",
    ),
]


def _module_routes(prefix: str, name: str, json_view, pdf_view, excel_view) -> list:
    """The three shapes of one report, under one prefix."""
    return [
        path(f"summaries/{prefix}/", json_view.as_view(), name=name),
        path(f"summaries/{prefix}/pdf/", pdf_view.as_view(), name=f"{name}-pdf"),
        path(f"summaries/{prefix}/excel/", excel_view.as_view(), name=f"{name}-excel"),
    ]


# The optional-module reports follow the modules themselves: registered only
# where the build ships the module (`api/urls.py` does the same for the
# module's own routes), and refused per institute by `Feature(...)` on the
# view. Off means 404, exactly like the module's other endpoints.
from .views.module_views import (  # noqa: E402 - after the money reports on purpose
    ComplaintsReportExcelView,
    ComplaintsReportPdfView,
    ComplaintsReportView,
    ContributionsReportExcelView,
    ContributionsReportPdfView,
    ContributionsReportView,
    CouponsReportExcelView,
    CouponsReportPdfView,
    CouponsReportView,
    LeaderboardReportExcelView,
    LeaderboardReportPdfView,
    LeaderboardReportView,
    PointDiscountsReportExcelView,
    PointDiscountsReportPdfView,
    PointDiscountsReportView,
)

if features.is_enabled(features.COMPLAINTS):
    urlpatterns += _module_routes(
        "complaints-report", "complaints-report",
        ComplaintsReportView, ComplaintsReportPdfView, ComplaintsReportExcelView,
    )

if features.is_enabled(features.CONTRIBUTIONS):
    urlpatterns += _module_routes(
        "contributions-report", "contributions-report",
        ContributionsReportView, ContributionsReportPdfView, ContributionsReportExcelView,
    )
    if features.is_enabled(features.CONTRIBUTION_POINTS):
        urlpatterns += _module_routes(
            "leaderboard-report", "leaderboard-report",
            LeaderboardReportView, LeaderboardReportPdfView, LeaderboardReportExcelView,
        )
        urlpatterns += _module_routes(
            "point-discounts-report", "point-discounts-report",
            PointDiscountsReportView, PointDiscountsReportPdfView,
            PointDiscountsReportExcelView,
        )

if features.is_enabled(features.COUPONS):
    urlpatterns += _module_routes(
        "coupons-report", "coupons-report",
        CouponsReportView, CouponsReportPdfView, CouponsReportExcelView,
    )
