"""
The optional-module reports as Excel workbooks.

Same theme, same helpers and the same discipline as `excel.py`: flat sheets
joined by ids, a header block that says which institute and school the file
belongs to and who asked for it, a Summary sheet with the totals, then one
sheet per list with a totals row under it. A spreadsheet is the artefact
most likely to be opened years later by somebody who never saw the screen,
so every sheet carries enough to stand on its own.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from openpyxl import Workbook

from ..periods import ReportFilters
from .excel import (
    BAD,
    BLUE,
    GOOD,
    GRAY,
    MONEY,
    PERCENT,
    WARN,
    _META_LABEL_FONT,
    _META_VALUE_FONT,
    _TITLE_FONT,
    _data_sheet,
    _date,
    _finish,
    _status_style,
    _table,
    _workbook,
)

COMPLAINT_STATUS_STYLES = {
    "submitted": WARN,
    "acknowledged": BLUE,
    "resolved": GOOD,
    "rejected": BAD,
    "withdrawn": GRAY,
}
CONTRIBUTION_STATUS_STYLES = {
    "pending": WARN,
    "under_review": BLUE,
    "approved": GOOD,
    "rejected": BAD,
    "duplicate": GRAY,
}
DISCOUNT_STATUS_STYLES = {
    "pending": WARN,
    "approved": GOOD,
    "rejected": BAD,
    "cancelled": GRAY,
}
COUPON_STATUS_STYLES = {
    "active": GOOD,
    "fully_redeemed": BLUE,
    "expired": GRAY,
    "revoked": BAD,
}
REDEMPTION_STATUS_STYLES = {
    "claimed": WARN,
    "applied": GOOD,
    "reversed": BAD,
}
BATCH_STATUS_STYLES = {"active": GOOD, "revoked": BAD}


def _stamp(value: Optional[str]) -> Optional[str]:
    """ISO timestamp -> `YYYY-MM-DD HH:MM`; empty for None."""
    return (value or "").replace("T", " ")[:16] or None


def _module_meta_sheet(
    workbook: Workbook,
    *,
    title: str,
    report: dict[str, Any],
    filters: ReportFilters,
    extra_rows: Sequence[tuple[str, Any]] = (),
):
    """
    The first sheet's header block for a module report: what this file is,
    whose it is, and how it was cut. School and institute both, because a
    file forwarded up the chain has to say where it came from.
    """
    sheet = workbook.create_sheet("Summary")
    sheet["A1"] = title
    sheet["A1"].font = _TITLE_FONT

    applied = report.get("filters", {})
    rows: list[tuple[str, Any]] = [
        ("School", report.get("school") or "—"),
        ("Institute", report.get("institute") or "All institutes"),
        ("Period", filters.label),
        ("System", applied.get("system_label") or "All systems"),
        ("Status", ", ".join(applied.get("statuses") or []) or "All statuses"),
        ("Program", filters.program_name or "All programs"),
        ("Set", filters.s_set or "All sets"),
        ("Year level", filters.year_level or "All year levels"),
        *extra_rows,
        ("Prepared by", report.get("prepared_by") or "—"),
        ("Generated", _stamp(report.get("generated_at"))),
    ]
    for offset, (label, value) in enumerate(rows):
        sheet.cell(row=2 + offset, column=1, value=label).font = _META_LABEL_FONT
        sheet.cell(row=2 + offset, column=2, value=value).font = _META_VALUE_FONT

    sheet.column_dimensions["A"].width = 32
    sheet.column_dimensions["B"].width = 44
    return sheet, 2 + len(rows) + 1


def _student_columns(row: dict[str, Any]) -> tuple:
    return (
        row.get("student_number"),
        row.get("student_name"),
        row.get("program"),
        row.get("year_level"),
        row.get("set"),
    )


STUDENT_HEADERS = ["Student No", "Student Name", "Program", "Year", "Set"]
STUDENT_WIDTHS = [14, 26, 34, 6, 6]


# --------------------------------------------------------------------------
# Complaints
# --------------------------------------------------------------------------


def build_complaints_workbook(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **_: Any,
) -> bytes:
    workbook = _workbook()
    sheet, row = _module_meta_sheet(
        workbook, title="Complaints Report", report=report, filters=filters
    )
    summary = report.get("summary", {})

    row = _table(
        sheet,
        start_row=row,
        headers=["Key figures", "Value"],
        rows=[
            ("Complaints", summary.get("total", 0)),
            ("Still open (submitted + being looked at)", summary.get("open", 0)),
            ("Submitted, not yet picked up", summary.get("submitted", 0)),
            ("Being looked at", summary.get("acknowledged", 0)),
            ("Resolved", summary.get("resolved", 0)),
            ("Closed without action", summary.get("rejected", 0)),
            ("Withdrawn by the student", summary.get("withdrawn", 0)),
            ("Naming a fee", summary.get("fee_related", 0)),
            ("Resolution rate (resolved of decided)", summary.get("resolution_rate")),
            ("Average days to close", summary.get("average_days_to_close")),
        ],
        band=False,
    )
    row = _table(
        sheet,
        start_row=row,
        headers=["By system", "Total", "Open", "Resolved", "Closed", "Withdrawn"],
        rows=[
            (r["label"], r["total"], r["open"], r["resolved"], r["rejected"], r["withdrawn"])
            for r in report.get("by_system", [])
        ],
        band=False,
    )
    _table(
        sheet,
        start_row=row,
        headers=["By category", "Total", "Open", "Resolved", "Closed", "Withdrawn"],
        rows=[
            (r["label"], r["total"], r["open"], r["resolved"], r["rejected"], r["withdrawn"])
            for r in report.get("by_category", [])
        ],
        band=False,
    )

    records = report.get("records", [])
    _data_sheet(
        workbook,
        "Complaints",
        headers=["ID", *STUDENT_HEADERS, "System", "Category", "Title", "Status",
                 "Fee ID", "Fee Category", "Fee Amount", "Fee Balance",
                 "Filed", "Handled By", "Handled", "Days Open", "Response",
                 "Description"],
        rows=[
            (r["id"], *_student_columns(r), r["system_label"], r["category_label"],
             r["title"], r["status_label"], r["fee_id"], r["fee_category"],
             r["fee_amount"], r["fee_balance"], _stamp(r["filed_at"]),
             r["handled_by"], _stamp(r["handled_at"]), r["days_open"],
             r["response"], r["description"])
            for r in records
        ],
        widths=[7, *STUDENT_WIDTHS, 26, 26, 36, 14, 8, 20, 12, 12, 17, 14, 17, 9,
                40, 50],
        formats={13: MONEY, 14: MONEY},
        styler=lambda r: {9: _status_by_label(r[9], COMPLAINT_STATUS_STYLES)},
        total_row=(f"Total ({len(records)} complaints)",) + (None,) * 20,
    )
    return _finish(workbook)


def _status_by_label(label: Any, styles: dict[str, tuple[str, str]]):
    """Labels are printed ("Being looked at"); match them back to a style."""
    text = str(label or "").lower()
    for key, style in styles.items():
        if key.replace("_", " ") in text or key in text:
            return style
    if "looked" in text:
        return BLUE
    if "closed" in text or "reject" in text:
        return BAD
    if "resolved" in text or "approved" in text or "applied" in text:
        return GOOD
    if "withdrawn" in text or "duplicate" in text or "cancel" in text or "expired" in text:
        return GRAY
    if "pending" in text or "submitted" in text or "claimed" in text:
        return WARN
    return None


# --------------------------------------------------------------------------
# Reports & ideas
# --------------------------------------------------------------------------


def build_contributions_workbook(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **_: Any,
) -> bytes:
    workbook = _workbook()
    sheet, row = _module_meta_sheet(
        workbook, title="Reports & Ideas Report", report=report, filters=filters
    )
    summary = report.get("summary", {})

    row = _table(
        sheet,
        start_row=row,
        headers=["Key figures", "Value"],
        rows=[
            ("Reports filed", summary.get("total", 0)),
            ("Pending review", summary.get("pending", 0)),
            ("Under review", summary.get("under_review", 0)),
            ("Approved", summary.get("approved", 0)),
            ("Rejected", summary.get("rejected", 0)),
            ("Duplicate", summary.get("duplicate", 0)),
            ("Points awarded", summary.get("points_awarded", 0)),
            ("Students who filed", summary.get("students", 0)),
            ("Approval rate (approved of decided)", summary.get("approval_rate")),
        ],
        band=False,
    )
    row = _table(
        sheet,
        start_row=row,
        headers=["By system", "Total", "Approved", "Pending", "Rejected", "Points"],
        rows=[
            (r["label"], r["total"], r["approved"], r["pending"], r["rejected"], r["points"])
            for r in report.get("by_system", [])
        ],
        band=False,
    )
    row = _table(
        sheet,
        start_row=row,
        headers=["By kind", "Total", "Approved", "Pending", "Rejected", "Points"],
        rows=[
            (r["label"], r["total"], r["approved"], r["pending"], r["rejected"], r["points"])
            for r in report.get("by_kind", [])
        ],
        band=False,
    )
    _table(
        sheet,
        start_row=row,
        headers=["By impact", "Total", "Approved", "Points"],
        rows=[
            (r["label"], r["total"], r["approved"], r["points"])
            for r in report.get("by_impact", [])
        ],
        band=False,
    )

    records = report.get("records", [])
    _data_sheet(
        workbook,
        "Reports",
        headers=["ID", *STUDENT_HEADERS, "System", "Kind", "Impact", "Title",
                 "Status", "Points", "Filed", "Reviewed By", "Reviewed",
                 "Review Remarks", "Description"],
        rows=[
            (r["id"], *_student_columns(r), r["system_label"], r["kind_label"],
             r["impact_label"], r["title"], r["status_label"], r["points_awarded"],
             _stamp(r["filed_at"]), r["reviewed_by"], _stamp(r["reviewed_at"]),
             r["review_remarks"], r["description"])
            for r in records
        ],
        widths=[7, *STUDENT_WIDTHS, 26, 20, 10, 36, 14, 8, 17, 14, 17, 36, 50],
        styler=lambda r: {10: _status_by_label(r[10], CONTRIBUTION_STATUS_STYLES)},
        total_row=(f"Total ({len(records)} reports)", None, None, None, None, None,
                   None, None, None, None, None,
                   sum(r["points_awarded"] or 0 for r in records
                       if r["status"] == "approved"),
                   None, None, None, None, None),
    )
    return _finish(workbook)


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------


def build_leaderboard_workbook(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **_: Any,
) -> bytes:
    workbook = _workbook()
    sheet, row = _module_meta_sheet(
        workbook,
        title="Contribution Leaderboard",
        report=report,
        filters=filters,
        extra_rows=[("Board", report.get("system_label") or "All systems")],
    )
    summary = report.get("summary", {})

    row = _table(
        sheet,
        start_row=row,
        headers=["Key figures", "Value"],
        rows=[
            ("Students on the board", summary.get("students", 0)),
            ("Points earned", summary.get("points_earned", 0)),
            ("Points spent", summary.get("points_spent", 0)),
            ("Points still held", summary.get("points_balance", 0)),
            ("Approved reports behind them", summary.get("approved_contributions", 0)),
        ],
        band=False,
    )
    _table(
        sheet,
        start_row=row,
        headers=["By system", "Students", "Earned", "Spent", "Held"],
        rows=[
            (r["label"], r["students"], r["points_earned"], r["points_spent"], r["points_balance"])
            for r in report.get("by_system", [])
        ],
        band=False,
    )

    rows = report.get("rows", [])
    _data_sheet(
        workbook,
        "Leaderboard",
        headers=["Rank", *STUDENT_HEADERS, "Earned", "Spent", "Held", "Approved Reports"],
        rows=[
            (r["rank"], *_student_columns(r), r["points_earned"], r["points_spent"],
             r["points_balance"], r["approved_contributions"])
            for r in rows
        ],
        widths=[7, *STUDENT_WIDTHS, 10, 10, 10, 16],
        styler=lambda r: {0: GOOD} if r[0] == 1 else {},
        total_row=(f"Total ({len(rows)} students)", None, None, None, None, None,
                   summary.get("points_earned", 0), summary.get("points_spent", 0),
                   summary.get("points_balance", 0),
                   summary.get("approved_contributions", 0)),
    )
    return _finish(workbook)


# --------------------------------------------------------------------------
# Point discounts
# --------------------------------------------------------------------------


def build_point_discounts_workbook(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **_: Any,
) -> bytes:
    workbook = _workbook()
    sheet, row = _module_meta_sheet(
        workbook, title="Point Discounts Report", report=report, filters=filters
    )
    summary = report.get("summary", {})

    row = _table(
        sheet,
        start_row=row,
        headers=["Key figures", "Value"],
        rows=[
            ("Requests", summary.get("requests", 0)),
            ("Awaiting approval", summary.get("pending", 0)),
            ("Applied", summary.get("approved", 0)),
            ("Rejected", summary.get("rejected", 0)),
            ("Cancelled", summary.get("cancelled", 0)),
            ("Points spent on applied discounts", summary.get("points_spent", 0)),
            ("Points set aside on pending requests", summary.get("points_pending", 0)),
            ("Amount taken off fees (applied)", summary.get("amount_approved", 0)),
            ("Amount awaiting approval", summary.get("amount_pending", 0)),
            ("Amount put back (cancelled)", summary.get("amount_cancelled", 0)),
            ("Students helped", summary.get("students_helped", 0)),
        ],
        band=False,
    )
    row = _table(
        sheet,
        start_row=row,
        headers=["By system", "Requests", "Applied", "Points", "Amount"],
        rows=[
            (r["label"], r["requests"], r["approved"], r["points"], r["amount"])
            for r in report.get("by_system", [])
        ],
        formats={4: MONEY},
        band=False,
    )
    _table(
        sheet,
        start_row=row,
        headers=["By fee category", "Requests", "Applied", "Points", "Amount"],
        rows=[
            (r["category"], r["requests"], r["approved"], r["points"], r["amount"])
            for r in report.get("by_category", [])
        ],
        formats={4: MONEY},
        band=False,
    )

    records = report.get("records", [])
    _data_sheet(
        workbook,
        "Discounts",
        headers=["ID", *STUDENT_HEADERS, "System", "Fee ID", "Fee Category", "Term",
                 "Fee Billed", "Fee Balance", "Fee Status", "Points", "Amount",
                 "Status", "Requested", "Reviewed By", "Reviewed", "Remarks",
                 "Cancelled By", "Cancelled", "Cancellation Reason"],
        rows=[
            (r["id"], *_student_columns(r), r["system_label"], r["fee_id"],
             r["fee_category"], r["fee_term"], r["fee_billed"], r["fee_balance"],
             r["fee_status"], r["points_spent"], r["amount"], r["status_label"],
             _stamp(r["requested_at"]), r["reviewed_by"], _stamp(r["reviewed_at"]),
             r["remarks"], r["cancelled_by"], _stamp(r["cancelled_at"]),
             r["cancellation_reason"])
            for r in records
        ],
        widths=[7, *STUDENT_WIDTHS, 26, 8, 22, 14, 12, 12, 10, 8, 12, 14, 17, 14,
                17, 30, 14, 17, 30],
        formats={10: MONEY, 11: MONEY, 14: MONEY},
        styler=lambda r: {15: _status_by_label(r[15], DISCOUNT_STATUS_STYLES)},
        total_row=(f"Total ({len(records)} requests)", None, None, None, None, None,
                   None, None, None, None, None, None, None,
                   sum(r["points_spent"] for r in records if r["status"] == "approved"),
                   round(sum(r["amount"] for r in records if r["status"] == "approved"), 2),
                   None, None, None, None, None, None, None, None),
    )
    return _finish(workbook)


# --------------------------------------------------------------------------
# Coupons
# --------------------------------------------------------------------------


def build_coupons_workbook(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **_: Any,
) -> bytes:
    workbook = _workbook()
    sheet, row = _module_meta_sheet(
        workbook, title="Coupons Report", report=report, filters=filters
    )
    summary = report.get("summary", {})

    row = _table(
        sheet,
        start_row=row,
        headers=["Key figures", "Value"],
        rows=[
            ("Batches", summary.get("batches", 0)),
            ("Active batches", summary.get("active_batches", 0)),
            ("Coupons issued", summary.get("issued", 0)),
            ("  bound to a student", summary.get("bound", 0)),
            ("  bearer", summary.get("bearer", 0)),
            ("Held, not yet applied", summary.get("claimed", 0)),
            ("Applied to a fee", summary.get("applied", 0)),
            ("Reversed", summary.get("reversed", 0)),
            ("Voided", summary.get("revoked", 0)),
            ("Expired", summary.get("expired", 0)),
            ("Students helped", summary.get("students_helped", 0)),
        ],
        band=False,
    )
    row = _table(
        sheet,
        start_row=row,
        headers=["Money", "Amount"],
        rows=[
            ("Taken off fees (applied)", summary.get("amount_applied", 0)),
            ("Put back (reversed)", summary.get("amount_reversed", 0)),
            ("Could still be spent (live, unspent, at face value)",
             summary.get("outstanding_value", 0)),
        ],
        formats={1: MONEY},
        band=False,
    )
    batches = report.get("batches", [])
    _table(
        sheet,
        start_row=row,
        headers=["Batch", "Value", "Applies To", "Term", "Valid From", "Expires",
                 "Status", "Issued", "Held", "Applied", "Reversed", "Voided",
                 "Unspent", "Amount Applied", "Could Still Cost", "Issued By",
                 "Reason", "Revocation Reason"],
        rows=[
            (b["name"], b["value_label"], b["applies_to"], b["term_label"],
             _date(b["valid_from"]), _date(b["expires_at"]), b["status_label"],
             b["issued"], b["claimed"], b["applied"], b["reversed"], b["revoked"],
             b["unspent"], b["amount_applied"],
             b["outstanding_value"] if b["outstanding_value"] is not None else "uncapped %",
             b["issued_by"], b["reason"], b["revocation_reason"])
            for b in batches
        ],
        widths=[28, 16, 24, 20, 11, 11, 9, 8, 7, 8, 9, 7, 8, 14, 14, 14, 30, 30],
        formats={13: MONEY, 14: MONEY},
        styler=lambda r: {6: _status_style(r[6], BATCH_STATUS_STYLES)},
        total_row=(f"Total ({len(batches)} batches)", None, None, None, None, None,
                   None, sum(b["issued"] for b in batches),
                   sum(b["claimed"] for b in batches),
                   sum(b["applied"] for b in batches),
                   sum(b["reversed"] for b in batches),
                   sum(b["revoked"] for b in batches),
                   sum(b["unspent"] for b in batches),
                   round(sum(b["amount_applied"] for b in batches), 2),
                   summary.get("outstanding_value", 0), None, None, None),
    )

    coupons = report.get("coupons", [])
    _data_sheet(
        workbook,
        "Coupons",
        headers=["Code", "Batch", "Value", "Bound", *STUDENT_HEADERS, "Uses",
                 "Spent", "Status", "Expires", "Voided", "Void Reason"],
        rows=[
            (c["code"], c["batch"], c["value_label"], "yes" if c["bound"] else "bearer",
             *_student_columns(c), f"{c['uses_taken']}/{c['max_redemptions']}",
             "yes" if c["is_spent"] else "no", c["status_label"], _date(c["expires_at"]),
             _stamp(c["revoked_at"]), c["revocation_reason"])
            for c in coupons
        ],
        widths=[18, 28, 16, 8, *STUDENT_WIDTHS, 7, 7, 16, 11, 17, 30],
        styler=lambda r: {11: _status_by_label(r[11], COUPON_STATUS_STYLES)},
        total_row=(f"Total ({len(coupons)} coupons)",) + (None,) * 14,
    )

    redemptions = report.get("redemptions", [])
    _data_sheet(
        workbook,
        "Redemptions",
        headers=["ID", "Code", "Batch", *STUDENT_HEADERS, "Fee ID", "Fee Category",
                 "Term", "Balance Before", "Amount", "Status", "Claimed",
                 "Applied", "Applied By", "Reversed By", "Reversed",
                 "Reversal Reason"],
        rows=[
            (r["id"], r["code"], r["batch"], *_student_columns(r), r["fee_id"],
             r["fee_category"], r["fee_term"], r["previous_balance"], r["amount"],
             r["status_label"], _stamp(r["claimed_at"]), _stamp(r["applied_at"]),
             r["applied_by"], r["reversed_by"], _stamp(r["reversed_at"]),
             r["reversal_reason"])
            for r in redemptions
        ],
        widths=[7, 18, 28, *STUDENT_WIDTHS, 8, 22, 14, 14, 12, 14, 17, 17, 14, 14,
                17, 30],
        formats={11: MONEY, 12: MONEY},
        styler=lambda r: {13: _status_by_label(r[13], REDEMPTION_STATUS_STYLES)},
        total_row=(f"Total ({len(redemptions)} redemptions)", None, None, None, None,
                   None, None, None, None, None, None, None,
                   round(sum(r["amount"] for r in redemptions if r["status"] == "applied"), 2),
                   None, None, None, None, None, None, None),
    )
    return _finish(workbook)


__all__ = [
    "build_complaints_workbook",
    "build_contributions_workbook",
    "build_leaderboard_workbook",
    "build_point_discounts_workbook",
    "build_coupons_workbook",
    "PERCENT",
]
