"""
Reports, printed.

One generic pipeline serves every report: `render_pdf()` takes a template
name and a context and returns bytes; `report_context()` builds the context
every template shares (the report itself, the period, who asked). Adding a
PDF for a new report means writing a template and pointing a view at it -
nothing in this module changes.

Templates live in `templates/reports/` and are normal Django templates - the
document's whole appearance is HTML and CSS, no Python.

The renderer is xhtml2pdf, which is pure Python (no GTK, no wkhtmltopdf, no
system packages) and therefore installs the same way on a developer's Windows
machine as it does on the deploy host. What it costs is CSS support: it
understands roughly CSS 2.1, so the templates are laid out with tables.
Flexbox and grid do nothing there - see the notes in `base_pdf.html`.
"""

from __future__ import annotations

import io
from typing import Any, Optional

from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..periods import ReportFilters


class PdfRenderingError(RuntimeError):
    """Raised when the HTML could not be turned into a PDF."""


def render_pdf(*, template_name: str, context: dict[str, Any]) -> bytes:
    """Render any report template into PDF bytes."""
    html = render_to_string(template_name, context)

    try:
        from xhtml2pdf import pisa
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise PdfRenderingError(
            "PDF export needs the `xhtml2pdf` package. It is in "
            "requirements.txt; run `pip install -r requirements.txt`."
        ) from exc

    buffer = io.BytesIO()
    result = pisa.CreatePDF(
        src=html,
        dest=buffer,
        encoding="utf-8",
        link_callback=_resolve_asset,
    )

    if result.err:
        raise PdfRenderingError(
            f"xhtml2pdf reported {result.err} error(s) while rendering "
            f"{template_name}."
        )

    return buffer.getvalue()


def report_context(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    school_name: Optional[str] = None,
    institute_acronym: Optional[str] = None,
) -> dict[str, Any]:
    """
    The context every report template shares.

    The full snapshot is available as `report`; the run details (period,
    filters, who asked, when) are top-level names so `base_pdf.html` can
    print them without knowing which report it is wrapping. `school_name`
    and `institute_acronym` are optional so older callers keep working; the
    page header prints them when present.
    """
    generated_at = parse_datetime(report.get("generated_at") or "") or timezone.now()

    return {
        "report": report,
        "generated_at": timezone.localtime(generated_at),
        "period_label": filters.label,
        "program_filter": filters.program_name,
        "set_filter": filters.s_set,
        "year_level_filter": filters.year_level,
        "system_filter": getattr(filters, "system", None),
        "status_filter": ", ".join(getattr(filters, "statuses", ()) or ()),
        "institute_name": institute_name,
        "institute_acronym": institute_acronym,
        "school_name": school_name,
        "prepared_by": prepared_by,
    }


def collection_report_context(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **identity: Any,
) -> dict[str, Any]:
    """
    The collection report's context: the shared base, plus each section
    lifted to a top-level name so its template reads as
    `{% for row in categories %}` rather than `{% for row in report.categories %}`.
    """
    context = report_context(
        report=report,
        filters=filters,
        institute_name=institute_name,
        prepared_by=prepared_by,
        **identity,
    )
    context.update(
        {
            "kpi": report.get("kpi", {}),
            "status_breakdown": report.get("status_breakdown", {}),
            "categories": report.get("categories", []),
            "losses": report.get("losses", {}),
            "payment_submissions": report.get("payment_submissions", {}),
            "staff": report.get("staff", []),
            "program_sets": report.get("program_sets", []),
            "payment_methods": report.get("payment_methods", []),
            "trend": report.get("trend", {}),
        }
    )
    return context


def render_collection_report_pdf(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    template_name: str = "reports/collection_report.html",
) -> bytes:
    """Convenience wrapper: the collection report straight to bytes."""
    return render_pdf(
        template_name=template_name,
        context=collection_report_context(
            report=report,
            filters=filters,
            institute_name=institute_name,
            prepared_by=prepared_by,
        ),
    )


def _resolve_asset(uri: str, rel: str) -> str:
    """
    Turn a URI in a template into something xhtml2pdf can open.

    Only needed if you put an image in a template. `{% static %}` paths are
    resolved to a file on disk; anything already absolute (an http(s) logo, a
    data: URI) is handed back untouched. A remote URL means a blocking network
    fetch while the PDF renders, so prefer a local file or a data: URI.
    """
    from django.conf import settings
    from django.contrib.staticfiles import finders

    if uri.startswith(("http://", "https://", "data:", "file://")):
        return uri

    static_url = getattr(settings, "STATIC_URL", "") or ""
    if static_url and uri.startswith(static_url):
        found = finders.find(uri.replace(static_url, "", 1))
        if found:
            return found if isinstance(found, str) else found[0]

    media_url = getattr(settings, "MEDIA_URL", "") or ""
    media_root = getattr(settings, "MEDIA_ROOT", "") or ""
    if media_url and media_root and uri.startswith(media_url):
        return str(media_root) + uri.replace(media_url, "", 1)

    return uri
