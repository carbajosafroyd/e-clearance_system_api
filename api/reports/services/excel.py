"""
Reports as Excel workbooks.

The same snapshot the JSON and PDF endpoints serve, translated into flat
sheets - Excel wants tables you can filter and pivot, not nesting, so a
nested report becomes several sheets (fees on one, payments on another,
submissions and their items on their own) joined by their IDs, each with a
totals row under its records.

One builder per report, all built on the small helpers at the top. The
styling follows the PDF theme: emerald headers, faint emerald banding,
money as `#,##0.00`. Change the hex values below and every workbook follows.

Conditional colour carries meaning on top of that theme, using Excel's own
good/neutral/bad palette so the workbooks read like native conditional
formatting: green for settled (paid fees, zero balances, cash), amber for
pending, red for overdue/cancelled/losses, blue for GCash, gray for waived.
Collection rates get a continuous red->amber->green ramp that only reaches
the full green at 100% - see `_rate_style()`.
"""

from __future__ import annotations

import io
from typing import Any, Callable, Optional, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..periods import ReportFilters
from .common import rate as _rate

# The PDF palette's hex values, sans '#' (openpyxl wants ARGB-ish strings).
EMERALD_800 = "065F46"
EMERALD_700 = "047857"
EMERALD_100 = "D1FAE5"
EMERALD_50 = "ECFDF5"
INK = "1F3A31"
MUTED = "5F7A6E"

MONEY = "#,##0.00"
PERCENT = '0.0"%"'

_TITLE_FONT = Font(bold=True, size=13, color=EMERALD_800)
_META_LABEL_FONT = Font(color=MUTED, size=9)
_META_VALUE_FONT = Font(color=INK, size=9)
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=9)
_HEADER_FILL = PatternFill("solid", fgColor=EMERALD_700)
_BAND_FILL = PatternFill("solid", fgColor=EMERALD_50)
_TOTAL_FONT = Font(bold=True, color=EMERALD_800)
_TOTAL_FILL = PatternFill("solid", fgColor=EMERALD_100)

# --------------------------------------------------------------------------
# Conditional colour
# --------------------------------------------------------------------------

# (fill, font) pairs - Excel's classic conditional-formatting trio plus a
# blue and a gray. A "style" everywhere below is one of these pairs.
GOOD = ("C6EFCE", "006100")  # settled: paid, zero balance, cash, active
WARN = ("FFEB9C", "9C6500")  # in flight: pending
BAD = ("FFC7CE", "9C0006")  # wrong or lost: overdue, cancelled, losses
BLUE = ("DDEBF7", "1F4E78")  # GCash
GRAY = ("E7E6E6", "595959")  # terminal but neutral: waived

FEE_STATUS_STYLES = {
    "paid": GOOD,
    "pending": WARN,
    "overdue": BAD,
    "waived": GRAY,
}
SUBMISSION_STATUS_STYLES = {
    "approved": GOOD,
    "pending": WARN,
    "rejected": BAD,
    "cancelled": GRAY,
    "withdrawn": GRAY,
}
PAYMENT_STATUS_STYLES = {"active": GOOD, "cancelled": BAD}
METHOD_STYLES = {"cash": GOOD, "gcash": BLUE}

# One PatternFill/Font per hex, shared across every cell that wears it.
_fills: dict[str, PatternFill] = {}
_fonts: dict[str, Font] = {}


def _fill(hex_color: str) -> PatternFill:
    if hex_color not in _fills:
        _fills[hex_color] = PatternFill("solid", fgColor=hex_color)
    return _fills[hex_color]


def _font(hex_color: str) -> Font:
    if hex_color not in _fonts:
        _fonts[hex_color] = Font(color=hex_color)
    return _fonts[hex_color]


def _status_style(
    value: Any, styles: dict[str, tuple[str, str]]
) -> Optional[tuple[str, str]]:
    return styles.get(str(value or "").lower())


def _blend(from_hex: str, to_hex: str, t: float) -> str:
    """Linear blend between two RRGGBB colours, t in [0, 1]."""
    channels = (
        round(
            int(from_hex[i : i + 2], 16)
            + (int(to_hex[i : i + 2], 16) - int(from_hex[i : i + 2], 16)) * t
        )
        for i in (0, 2, 4)
    )
    return "".join(f"{channel:02X}" for channel in channels)


def _rate_style(value: Any) -> Optional[tuple[str, str]]:
    """
    The collection-rate ramp: red at 0%, amber halfway, and a fill that
    keeps shifting toward green as the rate climbs - but the true green
    (Excel's "good" style) is reserved for exactly 100%, so a fully
    collected group is unmistakable at a glance.
    """
    if value is None:
        return None
    value = float(value)
    if value >= 100:
        return GOOD
    t = max(0.0, min(value, 100.0)) / 100.0
    if t <= 0.5:
        return (_blend(BAD[0], WARN[0], t * 2), INK)
    return (_blend(WARN[0], GOOD[0], (t - 0.5) * 2), INK)


def _zero_balance_style(balance: Any, billed: Any = 1) -> Optional[tuple[str, str]]:
    """Green for a settled balance - but not for a group that billed nothing."""
    try:
        settled = float(balance or 0) == 0 and float(billed or 0) > 0
    except (TypeError, ValueError):
        return None
    return GOOD if settled else None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _workbook() -> Workbook:
    workbook = Workbook()
    workbook.remove(workbook.active)  # the default sheet; every sheet is named
    return workbook


def _finish(workbook: Workbook) -> bytes:
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _institute_line(
    institute_name: Optional[str],
    school_name: Optional[str] = None,
    institute_acronym: Optional[str] = None,
) -> str:
    """
    "Institute of Computing (IC) - Davao del Norte State College".

    One cell rather than a School row of its own, so the Summary sheet keeps
    the row positions every reader of these files has learned.
    """
    if not institute_name:
        return "All institutes"
    line = institute_name
    if institute_acronym:
        line = f"{line} ({institute_acronym})"
    if school_name:
        line = f"{line} - {school_name}"
    return line


def _meta_sheet(
    workbook: Workbook,
    *,
    title: str,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str],
    prepared_by: Optional[str],
    school_name: Optional[str] = None,
    institute_acronym: Optional[str] = None,
):
    """The first sheet's header block: what this file is and how it was cut."""
    sheet = workbook.create_sheet("Summary")

    sheet["A1"] = title
    sheet["A1"].font = _TITLE_FONT

    rows = [
        ("Institute", _institute_line(institute_name, school_name, institute_acronym)),
        ("Period", filters.label),
        ("Program", filters.program_name or "All programs"),
        ("Set", filters.s_set or "All sets"),
        ("Year level", filters.year_level or "All year levels"),
        ("Prepared by", prepared_by or "—"),
        ("Generated", (report.get("generated_at") or "").replace("T", " ")[:16]),
    ]
    for offset, (label, value) in enumerate(rows):
        label_cell = sheet.cell(row=2 + offset, column=1, value=label)
        label_cell.font = _META_LABEL_FONT
        value_cell = sheet.cell(row=2 + offset, column=2, value=value)
        value_cell.font = _META_VALUE_FONT

    sheet.column_dimensions["A"].width = 30
    sheet.column_dimensions["B"].width = 42
    return sheet, 2 + len(rows) + 1  # first free row


def _table(
    sheet,
    *,
    start_row: int,
    headers: Sequence[str],
    rows: Sequence[Sequence[Any]],
    widths: Optional[Sequence[int]] = None,
    formats: Optional[dict[int, str]] = None,
    total_row: Optional[Sequence[Any]] = None,
    total_rows: Optional[Sequence[Sequence[Any]]] = None,
    band: bool = True,
    styler: Optional[
        Callable[[Sequence[Any]], dict[int, Optional[tuple[str, str]]]]
    ] = None,
) -> int:
    """
    Write one styled table. `formats` maps 0-based column index to a number
    format (MONEY / PERCENT). `styler` is called once per data row and
    returns {column index: (fill hex, font hex)} for the cells that should
    carry a conditional colour - it wins over the zebra banding. Use
    `total_rows` for several totals lines (Active / Cancelled / Grand);
    `total_row` is the single-line shorthand. Returns the first free row
    after the table.
    """
    for index, header in enumerate(headers):
        cell = sheet.cell(row=start_row, column=index + 1, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="left")

    row_number = start_row
    for row_offset, row in enumerate(rows):
        row_number = start_row + 1 + row_offset
        styles = styler(row) if styler else {}
        for index, value in enumerate(row):
            cell = sheet.cell(row=row_number, column=index + 1, value=value)
            if band and row_offset % 2 == 1:
                cell.fill = _BAND_FILL
            style = styles.get(index)
            if style:
                cell.fill = _fill(style[0])
                cell.font = _font(style[1])
            if formats and index in formats:
                cell.number_format = formats[index]

    if total_row is not None:
        total_rows = [total_row] + list(total_rows or [])
    for totals in total_rows or []:
        row_number += 1
        for index, value in enumerate(totals):
            cell = sheet.cell(row=row_number, column=index + 1, value=value)
            cell.font = _TOTAL_FONT
            cell.fill = _TOTAL_FILL
            if formats and index in formats:
                cell.number_format = formats[index]

    if widths:
        for index, width in enumerate(widths):
            sheet.column_dimensions[get_column_letter(index + 1)].width = width

    return row_number + 2


def _data_sheet(workbook: Workbook, title: str, **table_kwargs):
    """A sheet that is one table, with the header row frozen for scrolling."""
    sheet = workbook.create_sheet(title[:31])
    _table(sheet, start_row=1, **table_kwargs)
    sheet.freeze_panes = "A2"
    return sheet


def _date(value: Optional[str]) -> str:
    """ISO timestamp -> its date part; empty for None."""
    return (value or "")[:10]


def _sum(rows: Sequence[dict[str, Any]], key: str) -> float:
    return round(sum(row.get(key) or 0 for row in rows), 2)


# --------------------------------------------------------------------------
# Collection report
# --------------------------------------------------------------------------


def build_collection_report_workbook(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **identity: Any,
) -> bytes:
    workbook = _workbook()
    sheet, row = _meta_sheet(
        workbook,
        title="Collection Report",
        report=report,
        filters=filters,
        institute_name=institute_name,
        prepared_by=prepared_by,
        **identity,
    )

    kpi = report.get("kpi", {})
    status = report.get("status_breakdown", {})
    submissions = report.get("payment_submissions", {})
    losses = report.get("losses", {})
    waived = losses.get("waived", {})
    discounts = losses.get("discounts", {})
    discrepancies = losses.get("discrepancies", {})

    row = _table(
        sheet,
        start_row=row,
        headers=["Key figures", "Value"],
        rows=[
            ("Total collections", kpi.get("total_collections", 0)),
            ("Previous period", kpi.get("previous_total_collections")),
            ("Outstanding (waived excluded)", kpi.get("total_outstanding", 0)),
            ("Cash", kpi.get("cash", 0)),
            ("GCash", kpi.get("gcash", 0)),
            # Kept money, deliberately outside every collection figure - it
            # sits here rather than in the Losses block because it is not
            # a loss and must not look like part of that sum.
            ("Collected on later-waived fees (excluded)",
             waived.get("collected_before_waive", 0)),
            ("Discounted on later-waived fees (excluded)",
             waived.get("discounted_before_waive", 0)),
            ("Top category", kpi.get("top_category")),
            ("Top category rate", kpi.get("top_category_rate", 0)),
        ],
        formats={1: MONEY},
        band=False,
    )
    row = _table(
        sheet,
        start_row=row,
        headers=["Fees by status", "Count"],
        rows=[(name.capitalize(), status.get(name, 0))
              for name in ("paid", "pending", "overdue", "waived")],
        band=False,
    )
    # The three rows sum exactly to the Total losses line - anything that is
    # not a loss (like money collected before a waive) lives elsewhere. The
    # split under Discounts is a memo: it explains that line, it does not
    # add to the total.
    row = _table(
        sheet,
        start_row=row,
        headers=["Losses", "Count", "Amount"],
        rows=[
            ("Waived fees (forgiven balance)", waived.get("count", 0),
             waived.get("waived_amount", 0)),
            ("Discounts granted (points, coupons)", discounts.get("count", 0),
             discounts.get("amount", 0)),
            ("Payment shortfall (drawn down with no payment)",
             discrepancies.get("count", 0),
             discrepancies.get("shortfall", 0)),
        ],
        formats={2: MONEY},
        band=False,
        total_rows=[
            ("Total losses", None, losses.get("total_losses", 0)),
            ("  of which by contribution points", None,
             discounts.get("points", 0)),
            ("  of which by coupons", None, discounts.get("coupons", 0)),
            ("  of which unexplained by either ledger", None,
             discounts.get("other", 0)),
        ],
        styler=lambda r: {2: BAD} if r[2] else {},
    )
    _table(
        sheet,
        start_row=row,
        headers=["Submissions", "Count", "Amount"],
        rows=[
            (name.capitalize(),
             (submissions.get(name) or {}).get("count", 0),
             (submissions.get(name) or {}).get("amount", 0))
            for name in ("total", "approved", "pending", "rejected",
                         "cancelled", "withdrawn")
        ] + [("Approval rate", submissions.get("approval_rate"), None)],
        formats={2: MONEY},
        band=False,
    )

    categories = report.get("categories", [])
    # Gross and relief are carried whenever any exists. A spreadsheet is the
    # artefact most likely to be read years later by somebody with no idea
    # which schemes were switched on at the time, so "billed 500, discounted
    # 100, expected 400" beats an unexplained 400 - and the columns are
    # dropped entirely where every figure would be zero.
    shows_relief = _sum(categories, "discounts") > 0

    if shows_relief:
        category_headers = ["Category", "Rate", "Billed", "Discounts", "Expected",
                            "Collected", "Outstanding", "Fees", "Paid",
                            "Pending", "Overdue", "Waived"]
        category_rows = [
            (c["category_name"], c["collection_rate"], c["billed"],
             c["discounts"], c["expected"], c["collected"], c["outstanding"],
             c["fee_count"], c["paid_count"], c["pending_count"],
             c["overdue_count"], c["waived_count"])
            for c in categories
        ]
        category_widths = [28, 9, 14, 13, 14, 14, 14, 7, 7, 9, 9, 8]
        category_formats = {1: PERCENT, 2: MONEY, 3: MONEY, 4: MONEY, 5: MONEY,
                            6: MONEY}
        category_total = (
            "All categories",
            _rate(_sum(categories, "collected"), _sum(categories, "expected")),
            _sum(categories, "billed"), _sum(categories, "discounts"),
            _sum(categories, "expected"), _sum(categories, "collected"),
            _sum(categories, "outstanding"), _sum(categories, "fee_count"),
            _sum(categories, "paid_count"), _sum(categories, "pending_count"),
            _sum(categories, "overdue_count"), _sum(categories, "waived_count"),
        )
    else:
        category_headers = ["Category", "Rate", "Expected", "Collected",
                            "Outstanding", "Fees", "Paid", "Pending",
                            "Overdue", "Waived"]
        category_rows = [
            (c["category_name"], c["collection_rate"], c["expected"],
             c["collected"], c["outstanding"], c["fee_count"], c["paid_count"],
             c["pending_count"], c["overdue_count"], c["waived_count"])
            for c in categories
        ]
        category_widths = [28, 9, 14, 14, 14, 7, 7, 9, 9, 8]
        category_formats = {1: PERCENT, 2: MONEY, 3: MONEY, 4: MONEY}
        category_total = (
            "All categories",
            _rate(_sum(categories, "collected"), _sum(categories, "expected")),
            _sum(categories, "expected"), _sum(categories, "collected"),
            _sum(categories, "outstanding"), _sum(categories, "fee_count"),
            _sum(categories, "paid_count"), _sum(categories, "pending_count"),
            _sum(categories, "overdue_count"), _sum(categories, "waived_count"),
        )

    _data_sheet(
        workbook,
        "Categories",
        headers=category_headers,
        rows=category_rows,
        widths=category_widths,
        formats=category_formats,
        styler=lambda r: {1: _rate_style(r[1])},
        total_row=category_total,
    )
    program_sets = report.get("program_sets", [])
    _data_sheet(
        workbook,
        "Programs & Sets",
        headers=["Program", "Year level", "Set", "Billed", "Collected",
                 "Balance", "Rate"],
        rows=[
            (r["program"], r["year_level"], r["set"], r["billed"], r["total"],
             r["balance"], r["rate"])
            for r in program_sets
        ],
        widths=[38, 10, 8, 14, 14, 14, 9],
        formats={3: MONEY, 4: MONEY, 5: MONEY, 6: PERCENT},
        styler=lambda r: {
            5: _zero_balance_style(r[5], r[3]),
            6: _rate_style(r[6]),
        },
        total_row=(
            "All programs", None, None,
            _sum(program_sets, "billed"), _sum(program_sets, "total"),
            _sum(program_sets, "balance"),
            _rate(_sum(program_sets, "total"), _sum(program_sets, "billed")),
        ),
    )
    staff = report.get("staff", [])
    _data_sheet(
        workbook,
        "Staff",
        headers=["Staff", "Transactions", "Collected"],
        rows=[
            (s["staff_username"], s["tx_count"], s["collected"])
            for s in staff
        ],
        widths=[24, 13, 16],
        formats={2: MONEY},
        total_row=("All staff", _sum(staff, "tx_count"),
                   _sum(staff, "collected")),
    )
    months = report.get("payment_methods", [])
    _data_sheet(
        workbook,
        "Monthly",
        headers=["Month", "Cash", "GCash", "Total"],
        rows=[
            (m["period"], m["cash"], m["gcash"],
             m.get("total", round(m["cash"] + m["gcash"], 2)))
            for m in months
        ],
        widths=[12, 14, 14, 14],
        formats={1: MONEY, 2: MONEY, 3: MONEY},
        total_row=("Grand total", _sum(months, "cash"), _sum(months, "gcash"),
                   round(_sum(months, "cash") + _sum(months, "gcash"), 2)),
    )

    # The trend as a matrix: one row per month, one column per category, with
    # each category's total underneath its own column.
    trend = report.get("trend", {})
    series = trend.get("series", [])
    _data_sheet(
        workbook,
        "Trend",
        headers=["Month"] + [s["name"] for s in series],
        rows=[
            [label] + [s["points"][index] for s in series]
            for index, label in enumerate(trend.get("labels", []))
        ],
        widths=[12] + [18] * len(series),
        formats={index + 1: MONEY for index in range(len(series))},
        total_row=["Total"] + [round(sum(s["points"]), 2) for s in series],
    )

    _collection_losses_sheet(workbook, waived, discounts, discrepancies)

    return _finish(workbook)


def _collection_losses_sheet(workbook, waived, discounts, discrepancies):
    """
    The losses tab: the waived fees the money math excludes, the fees that
    got relief and what funded it, then the fees whose payments do not
    reconcile with what the fee says was paid off.
    """
    sheet = workbook.create_sheet("Losses")

    row = _table(
        sheet,
        start_row=1,
        headers=["Waived Fee", "Category", "Student No", "Student Name",
                 "Program", "Year", "Set", "Amount", "Paid Before Waive",
                 "Waived (Loss)", "Reason"],
        rows=[
            (r["fee_id"], r["category"], r["student_number"],
             r["student_name"], r["program"], r["year_level"], r["set"],
             r["total_amount"], r["paid_before_waive"], r["waived_amount"],
             r["remarks"])
            for r in waived.get("records", [])
        ],
        # The tables below share these sheet columns, so the widths are a
        # compromise between the layouts.
        widths=[13, 22, 14, 26, 34, 12, 12, 14, 15, 15, 30],
        formats={7: MONEY, 8: MONEY, 9: MONEY},
        styler=lambda r: {9: BAD},
        total_row=(
            f"Total ({waived.get('count', 0)} fees)", None, None, None, None,
            None, None, waived.get("billed", 0),
            waived.get("collected_before_waive", 0),
            waived.get("waived_amount", 0), None,
        ),
    )

    # Relief, fee by fee, split by what funded it. `Other` is whatever the
    # two ledgers do not explain - normally zero, and worth a look when not.
    row = _table(
        sheet,
        start_row=row,
        headers=["Discounted Fee", "Category", "Student No", "Student Name",
                 "Program", "Year", "Set", "Billed", "Points", "Coupons",
                 "Other", "Discount (Loss)", "Balance", "Status"],
        rows=[
            (r["fee_id"], r["category"], r["student_number"],
             r["student_name"], r["program"], r["year_level"], r["set"],
             r["total_amount"], r["points"], r["coupons"], r["other"],
             r["discount_total"], r["balance"], r["status"])
            for r in discounts.get("records", [])
        ],
        formats={7: MONEY, 8: MONEY, 9: MONEY, 10: MONEY, 11: MONEY,
                 12: MONEY},
        styler=lambda r: {
            10: BAD if r[10] else None,
            11: BAD,
            13: _status_style(r[13], FEE_STATUS_STYLES),
        },
        total_row=(
            f"Total ({discounts.get('count', 0)} fees)", None, None, None,
            None, None, None, None, discounts.get("points", 0),
            discounts.get("coupons", 0), discounts.get("other", 0),
            discounts.get("amount", 0), None, None,
        ),
    )

    _table(
        sheet,
        start_row=row,
        headers=["Discrepancy Fee", "Category", "Student No", "Student Name",
                 "Status", "Amount", "Balance", "Fee Says Paid",
                 "Payments Total", "Approved Submissions", "Difference"],
        rows=[
            (r["fee_id"], r["category"], r["student_number"],
             r["student_name"], r["status"], r["total_amount"], r["balance"],
             r["fee_paid"], r["payments_total"],
             r["approved_submission_total"], r["difference"])
            for r in discrepancies.get("records", [])
        ],
        formats={5: MONEY, 6: MONEY, 7: MONEY, 8: MONEY, 9: MONEY, 10: MONEY},
        styler=lambda r: {
            4: _status_style(r[4], FEE_STATUS_STYLES),
            10: BAD,
        },
        total_row=(
            f"Total ({discrepancies.get('count', 0)} fees)", None, None, None,
            None, None, None, None, None, None,
            discrepancies.get("difference", 0),
        ),
    )
    sheet.freeze_panes = "A2"
    return sheet


# --------------------------------------------------------------------------
# Detailed fees
# --------------------------------------------------------------------------


def build_detailed_fees_workbook(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **identity: Any,
) -> bytes:
    workbook = _workbook()
    sheet, row = _meta_sheet(
        workbook,
        title="Detailed Fees",
        report=report,
        filters=filters,
        institute_name=institute_name,
        prepared_by=prepared_by,
        **identity,
    )

    summary = report.get("summary", {})
    status = summary.get("status_breakdown", {})
    submissions = summary.get("payment_submissions", {})
    totals = report.get("totals", {})
    losses = report.get("losses", {})

    payments_section = report.get("payments", {})
    payment_totals = payments_section.get("totals", {})
    active_totals = payment_totals.get("active", {})
    cancelled_totals = payment_totals.get("cancelled", {})

    row = _table(
        sheet,
        start_row=row,
        headers=["Fees by status", "Count"],
        rows=[(name.capitalize(), status.get(name, 0))
              for name in ("paid", "pending", "overdue", "waived")],
        band=False,
    )
    row = _table(
        sheet,
        start_row=row,
        headers=["Payments", "Value"],
        rows=[
            ("Active transactions", active_totals.get("count", 0)),
            ("Active cash", active_totals.get("cash", 0)),
            ("Active GCash", active_totals.get("gcash", 0)),
            ("Active total", active_totals.get("amount_paid", 0)),
            ("Cancelled transactions", cancelled_totals.get("count", 0)),
            ("Cancelled amount", cancelled_totals.get("amount_paid", 0)),
        ],
        formats={1: MONEY},
        band=False,
        total_row=("Grand total (active + cancelled)",
                   payment_totals.get("amount_paid", 0)),
    )
    row = _table(
        sheet,
        start_row=row,
        headers=["Losses (waived fees)", "Value"],
        rows=[
            ("Waived fees", losses.get("count", 0)),
            ("Billed on them", losses.get("billed", 0)),
            ("Collected before waiving", losses.get("collected_before_waive", 0)),
        ],
        formats={1: MONEY},
        band=False,
        total_row=("Total losses (forgiven balance)",
                   losses.get("waived_amount", 0)),
    )
    row = _table(
        sheet,
        start_row=row,
        headers=["Submissions", "Count", "Amount"],
        rows=[
            (name.capitalize(),
             (submissions.get(name) or {}).get("count", 0),
             (submissions.get(name) or {}).get("amount", 0))
            for name in ("total", "approved", "pending", "rejected",
                         "cancelled", "withdrawn")
        ] + [("Approval rate", submissions.get("approval_rate"), None)],
        formats={2: MONEY},
        band=False,
    )
    _table(
        sheet,
        start_row=row,
        headers=["Grand total", "Billed", "Paid", "Balance", "Rate"],
        rows=[],
        total_row=("All categories (waived excluded)", totals.get("billed", 0),
                   totals.get("paid", 0), totals.get("balance", 0),
                   totals.get("collection_rate", 0)),
        formats={1: MONEY, 2: MONEY, 3: MONEY, 4: PERCENT},
    )

    # ---- Fees: one row per fee, with a straight-sum totals row below ------
    # Relief gets its own column: "billed 500, discounted 100, paid 400" is
    # the line somebody reading this in a year can follow, and a Paid figure
    # that silently sits 100 short of Amount is not.
    fee_rows = []
    fee_amount = fee_discount = fee_paid = fee_balance = 0.0
    for category in report.get("categories", []):
        for group in category.get("statuses", []):
            for fee in group["fees"]:
                discount = fee.get("discount_total", 0) or 0
                fee_amount += fee["total_amount"]
                fee_discount += discount
                fee_paid += fee["paid"]
                fee_balance += fee["balance"]
                fee_rows.append(
                    (fee["id"], category["category_name"], fee["status"],
                     fee["student_number"], fee["student_name"], fee["program"],
                     fee["year_level"], fee["set"], fee["total_amount"],
                     discount, fee["paid"], fee["balance"],
                     _date(fee["due_date"]), _date(fee["issued_at"]),
                     fee["issued_by"], fee["academic_year"], fee["semester"],
                     fee["remarks"])
                )

    _data_sheet(
        workbook,
        "Fees",
        headers=["Fee ID", "Category", "Status", "Student No", "Student Name",
                 "Program", "Year Level", "Set", "Amount", "Discount", "Paid",
                 "Balance", "Due Date", "Date Issued", "Issued By",
                 "Academic Year", "Semester", "Remarks"],
        rows=fee_rows,
        widths=[8, 22, 10, 14, 26, 34, 10, 7, 12, 12, 12, 12, 11, 11, 14, 13,
                10, 28],
        formats={8: MONEY, 9: MONEY, 10: MONEY, 11: MONEY},
        styler=lambda r: {
            2: _status_style(r[2], FEE_STATUS_STYLES),
            9: BAD if r[9] else None,
            11: _zero_balance_style(r[11], r[8]),
        },
        total_row=(f"Total ({len(fee_rows)} fees)", None, None, None, None,
                   None, None, None, round(fee_amount, 2),
                   round(fee_discount, 2), round(fee_paid, 2),
                   round(fee_balance, 2), None, None, None, None, None, None),
    )

    # ---- Payments: every payment, direct or submission-born, cancelled
    # ones included and flagged ---------------------------------------------
    _data_sheet(
        workbook,
        "Payments",
        headers=["Payment ID", "Date Paid", "Fee ID", "Category", "Student No",
                 "Student Name", "Source", "Submission Ref", "Method",
                 "Previous Balance", "Amount Paid", "Received By", "Status",
                 "Cancelled By", "Cancellation Reason"],
        rows=[
            (p["id"], _date(p["created_at"]), p["fee_id"], p["category"],
             p["student_number"], p["student_name"], p["source"],
             p["submission_reference"], p["payment_method"],
             p["previous_balance"], p["amount_paid"], p["received_by"],
             p["status"], p["cancelled_by"], p["cancellation_reason"])
            for p in payments_section.get("records", [])
        ],
        widths=[11, 11, 8, 22, 14, 26, 11, 16, 9, 15, 12, 14, 10, 14, 28],
        formats={9: MONEY, 10: MONEY},
        styler=lambda r: {
            8: _status_style(r[8], METHOD_STYLES),
            12: _status_style(r[12], PAYMENT_STATUS_STYLES),
        },
        total_rows=[
            (f"Active ({active_totals.get('count', 0)} payments)", None, None,
             None, None, None, None, None, None, None,
             active_totals.get("amount_paid", 0), None, None, None, None),
            (f"Cancelled ({cancelled_totals.get('count', 0)} payments)", None,
             None, None, None, None, None, None, None, None,
             cancelled_totals.get("amount_paid", 0), None, None, None, None),
            (f"Grand total ({payment_totals.get('count', 0)} payments)", None,
             None, None, None, None, None, None, None, None,
             payment_totals.get("amount_paid", 0), None, None, None, None),
        ],
    )

    # ---- Payment Submissions: one row per submission, never per fee -------
    submissions_section = report.get("payment_submissions", {})
    submission_totals = submissions_section.get("totals", {})
    _data_sheet(
        workbook,
        "Payment Submissions",
        headers=["Submission ID", "Date Submitted", "Student No",
                 "Student Name", "Reference No", "Status", "Total Amount",
                 "Fees Covered", "Reviewed By", "Reviewed At", "Remarks",
                 "Screenshot Links"],
        rows=[
            (s["id"], _date(s["submitted_at"]), s["student_number"],
             s["student_name"], s["reference_number"], s["status"],
             s["total_amount_paid"], s["fee_count"], s["reviewed_by"],
             _date(s["reviewed_at"]), s["remarks"],
             ", ".join(s["screenshot_urls"]) or None)
            for s in submissions_section.get("records", [])
        ],
        widths=[13, 14, 14, 26, 16, 10, 13, 12, 14, 12, 24, 46],
        formats={6: MONEY},
        styler=lambda r: {5: _status_style(r[5], SUBMISSION_STATUS_STYLES)},
        total_row=(
            f"Total ({submission_totals.get('count', 0)} submissions)", None,
            None, None, None, None,
            submission_totals.get("total_amount_paid", 0), None, None, None,
            None, None),
    )

    # ---- Payment Submission Items: each fee's slice of a submission -------
    items_section = report.get("payment_submission_items", {})
    item_totals = items_section.get("totals", {})
    _data_sheet(
        workbook,
        "Payment Submission Items",
        headers=["Item ID", "Submission ID", "Reference No",
                 "Submission Status", "Fee ID", "Category", "Student No",
                 "Student Name", "Previous Balance", "Amount Paid"],
        rows=[
            (i["id"], i["payment_submission_id"], i["submission_reference"],
             i["submission_status"], i["fee_id"], i["category"],
             i["student_number"], i["student_name"], i["previous_balance"],
             i["amount_paid"])
            for i in items_section.get("records", [])
        ],
        widths=[8, 13, 16, 16, 8, 22, 14, 26, 15, 12],
        formats={8: MONEY, 9: MONEY},
        styler=lambda r: {3: _status_style(r[3], SUBMISSION_STATUS_STYLES)},
        total_row=(f"Total ({item_totals.get('count', 0)} items)", None, None,
                   None, None, None, None, None, None,
                   item_totals.get("amount_paid", 0)),
    )

    _detailed_losses_sheet(workbook, losses)

    return _finish(workbook)


def _detailed_losses_sheet(workbook, losses):
    """
    The losses tab: every waived fee, then the payments and submission items
    behind those fees, so the money that came in before the rest was
    forgiven is fully traceable.
    """
    sheet = workbook.create_sheet("Losses")
    records = losses.get("records", [])

    row = _table(
        sheet,
        start_row=1,
        headers=["Fee ID", "Category", "Student No", "Student Name", "Program",
                 "Year", "Set", "Amount", "Paid Before Waive", "Waived (Loss)",
                 "Reason"],
        rows=[
            (r["fee_id"], r["category"], r["student_number"],
             r["student_name"], r["program"], r["year_level"], r["set"],
             r["total_amount"], r["paid_before_waive"], r["waived_amount"],
             r["remarks"])
            for r in records
        ],
        # The payments and items tables below share these sheet columns, so
        # the widths are a compromise between the three layouts.
        widths=[13, 22, 16, 26, 34, 14, 14, 14, 15, 15, 30],
        formats={7: MONEY, 8: MONEY, 9: MONEY},
        styler=lambda r: {9: BAD},
        total_row=(
            f"Total ({losses.get('count', 0)} waived fees)", None, None, None,
            None, None, None, losses.get("billed", 0),
            losses.get("collected_before_waive", 0),
            losses.get("waived_amount", 0), None,
        ),
    )

    payment_rows = [
        (r["fee_id"], p["id"], _date(p["created_at"]), p["payment_method"],
         p["status"], p["submission_reference"], p["previous_balance"],
         p["amount_paid"], p["received_by"])
        for r in records
        for p in r["payments"]
    ]
    row = _table(
        sheet,
        start_row=row,
        headers=["Fee ID", "Payment ID", "Date Paid", "Method", "Status",
                 "Submission Ref", "Previous Balance", "Amount Paid",
                 "Received By"],
        rows=payment_rows,
        formats={6: MONEY, 7: MONEY},
        styler=lambda r: {
            3: _status_style(r[3], METHOD_STYLES),
            4: _status_style(r[4], PAYMENT_STATUS_STYLES),
        },
        total_row=(
            f"Total ({len(payment_rows)} payments)", None, None, None, None,
            None, None,
            round(sum(p[7] for p in payment_rows), 2), None,
        ),
    )

    item_rows = [
        (r["fee_id"], i["item_id"], _date(i["submitted_at"]),
         i["submission_reference"], i["submission_status"],
         i["previous_balance"], i["amount_paid"])
        for r in records
        for i in r["submission_items"]
    ]
    _table(
        sheet,
        start_row=row,
        headers=["Fee ID", "Item ID", "Date Submitted", "Submission Ref",
                 "Submission Status", "Previous Balance", "Amount Paid"],
        rows=item_rows,
        formats={5: MONEY, 6: MONEY},
        styler=lambda r: {4: _status_style(r[4], SUBMISSION_STATUS_STYLES)},
        total_row=(
            f"Total ({len(item_rows)} items)", None, None, None, None, None,
            round(sum(i[6] for i in item_rows), 2),
        ),
    )
    sheet.freeze_panes = "A2"
    return sheet


# --------------------------------------------------------------------------
# By staff
# --------------------------------------------------------------------------


def build_staff_workbook(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **identity: Any,
) -> bytes:
    workbook = _workbook()
    sheet, row = _meta_sheet(
        workbook,
        title="Collections by Staff",
        report=report,
        filters=filters,
        institute_name=institute_name,
        prepared_by=prepared_by,
        **identity,
    )

    grand = report.get("totals", {})
    _table(
        sheet,
        start_row=row,
        headers=["Staff", "Full name", "Transactions", "Cash", "GCash",
                 "Collected", "Share"],
        rows=[
            (s["staff_username"], s["full_name"], s["totals"]["tx_count"],
             s["totals"]["cash"], s["totals"]["gcash"],
             s["totals"]["collected"], s["totals"]["share_of_total"])
            for s in report.get("staff", [])
        ],
        widths=[16, 24, 13, 13, 13, 14, 9],
        formats={3: MONEY, 4: MONEY, 5: MONEY, 6: PERCENT},
        total_row=("All staff", None, grand.get("tx_count", 0),
                   grand.get("cash", 0), grand.get("gcash", 0),
                   grand.get("collected", 0), None),
    )

    payment_rows = [
        (s["staff_username"], _date(p["created_at"]), p["student_number"],
         p["student_name"], p["category"], p["payment_method"],
         p["submission_reference"], p["previous_balance"], p["amount_paid"])
        for s in report.get("staff", [])
        for p in s["payments"]
    ]
    _data_sheet(
        workbook,
        "Payments",
        headers=["Staff", "Date", "Student No", "Student", "Category",
                 "Method", "Reference", "Previous balance", "Amount"],
        rows=payment_rows,
        widths=[16, 11, 13, 26, 22, 9, 16, 15, 12],
        formats={7: MONEY, 8: MONEY},
        styler=lambda r: {5: _status_style(r[5], METHOD_STYLES)},
        total_row=(f"Total ({len(payment_rows)} payments)", None, None, None,
                   None, None, None, None, grand.get("collected", 0)),
    )

    return _finish(workbook)


# --------------------------------------------------------------------------
# Audit logs
# --------------------------------------------------------------------------


AUDIT_ACTION_STYLES = {
    "create": GOOD,
    "approve": GOOD,
    "update": BLUE,
    "cancel": WARN,
    "reject": BAD,
    "delete": BAD,
}


def build_audit_logs_workbook(
    *,
    report: dict[str, Any],
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    school_name: Optional[str] = None,
    institute_acronym: Optional[str] = None,
) -> bytes:
    """
    The audit-log report as a workbook. Takes the snapshot
    `api.audits.report.build_audit_log_report` returns; lives here so every
    export in the project shares one theme and one set of table helpers.
    """
    import json

    workbook = _workbook()
    sheet = workbook.create_sheet("Summary")

    sheet["A1"] = "Audit Log Report"
    sheet["A1"].font = _TITLE_FONT

    applied = report.get("applied_filters", {})
    summary = report.get("summary", {})
    meta_rows = [
        ("Institute", _institute_line(institute_name, school_name, institute_acronym)),
        ("Period", applied.get("period", "All time")),
        ("Systems", applied.get("systems", "All systems")),
        ("Actions", applied.get("actions", "All actions")),
        ("Actor", applied.get("actor", "All actors")),
        ("Actor types", applied.get("actor_types", "All actor types")),
        ("Entity", applied.get("entity", "All entities")),
        ("Prepared by", prepared_by or "—"),
        ("Generated", (report.get("generated_at") or "").replace("T", " ")[:16]),
    ]
    for offset, (label, value) in enumerate(meta_rows):
        sheet.cell(row=2 + offset, column=1, value=label).font = _META_LABEL_FONT
        sheet.cell(row=2 + offset, column=2, value=value).font = _META_VALUE_FONT
    sheet.column_dimensions["A"].width = 30
    sheet.column_dimensions["B"].width = 42
    row = 2 + len(meta_rows) + 1

    row = _table(
        sheet,
        start_row=row,
        headers=["Key figures", "Value"],
        rows=[
            ("Total entries", summary.get("total_entries", 0)),
            ("First entry", (summary.get("first_entry_at") or "—")[:10]),
            ("Latest entry", (summary.get("last_entry_at") or "—")[:10]),
            ("Entries listed", summary.get("records_returned", 0)),
            ("Entry list truncated", "yes" if summary.get("records_truncated") else "no"),
        ],
        band=False,
    )
    for title, key in (
        ("By action", "by_action"),
        ("By system", "by_system"),
        ("By actor type", "by_actor_type"),
    ):
        row = _table(
            sheet,
            start_row=row,
            headers=[title, "Entries"],
            rows=[(item["label"], item["count"]) for item in report.get(key, [])],
            band=False,
        )
    _table(
        sheet,
        start_row=row,
        headers=["By entity", "Entries"],
        rows=[
            (item["entity_type"], item["count"])
            for item in report.get("by_entity_type", [])
        ],
        band=False,
    )

    _data_sheet(
        workbook,
        "Top Actors",
        headers=["Username", "Full name", "Actor type", "Actor ID", "Entries"],
        rows=[
            (a["actor_username"], a["actor_full_name"], a["actor_type"],
             a["actor_id"], a["entry_count"])
            for a in report.get("top_actors", [])
        ],
        widths=[20, 28, 12, 10, 10],
    )

    _data_sheet(
        workbook,
        "Daily Activity",
        headers=["Date", "Entries"],
        rows=[(d["date"], d["count"]) for d in report.get("daily", [])],
        widths=[14, 10],
        total_row=("Total", sum(d["count"] for d in report.get("daily", []))),
    )

    def _values(payload) -> Optional[str]:
        return json.dumps(payload, ensure_ascii=False) if payload else None

    records = report.get("records", [])
    _data_sheet(
        workbook,
        "Entries",
        headers=["ID", "Date & Time", "System", "Entity", "Entity ID", "Action",
                 "Actor Type", "Actor ID", "Username", "Actor Name",
                 "Old Values", "New Values"],
        rows=[
            (r["id"], r["created_at_display"], r["system"], r["entity_type"],
             r["entity_id"], r["action"], r["actor_type"], r["actor_id"],
             r["actor_username"], r["actor_full_name"],
             _values(r["old_values"]), _values(r["new_values"]))
            for r in records
        ],
        widths=[8, 17, 11, 20, 10, 10, 11, 9, 16, 24, 46, 46],
        styler=lambda r: {5: _status_style(r[5], AUDIT_ACTION_STYLES)},
        total_row=(f"Total ({len(records)} entries listed)", None, None, None,
                   None, None, None, None, None, None, None, None),
    )

    return _finish(workbook)


# --------------------------------------------------------------------------
# By program, year level and set
# --------------------------------------------------------------------------


def build_program_set_workbook(
    *,
    report: dict[str, Any],
    filters: ReportFilters,
    institute_name: Optional[str] = None,
    prepared_by: Optional[str] = None,
    **identity: Any,
) -> bytes:
    workbook = _workbook()
    sheet, row = _meta_sheet(
        workbook,
        title="Collections by Program",
        report=report,
        filters=filters,
        institute_name=institute_name,
        prepared_by=prepared_by,
        **identity,
    )

    # Relief is carried at every level - group, student, fee - so the
    # arithmetic reads the same way top to bottom: billed - discounts - paid
    # = balance. A totals block without it would leave Paid unexplainably
    # short of Billed on any group that granted a discount.
    summary_rows, student_rows, fee_rows = [], [], []
    fee_amount = fee_discount = fee_paid = fee_balance = 0.0
    for program in report.get("programs", []):
        for level in program["year_levels"]:
            for group in level["sets"]:
                summary_rows.append(
                    (program["program"], level["year_level"], group["set"],
                     group["totals"]["student_count"],
                     group["totals"]["billed"],
                     group["totals"].get("discounts", 0),
                     group["totals"]["paid"],
                     group["totals"]["balance"],
                     group["totals"]["collection_rate"])
                )
                for student in group["students"]:
                    student_rows.append(
                        (program["program"], level["year_level"], group["set"],
                         student["student_number"], student["student_name"],
                         student["totals"]["billed"],
                         student["totals"].get("discounts", 0),
                         student["totals"]["paid"],
                         student["totals"]["balance"],
                         student["totals"]["collection_rate"])
                    )
                    for fee in student["fees"]:
                        discount = fee.get("discount_total", 0) or 0
                        fee_amount += fee["total_amount"]
                        fee_discount += discount
                        fee_paid += fee["paid"]
                        fee_balance += fee["balance"]
                        fee_rows.append(
                            (program["program"], level["year_level"],
                             group["set"], student["student_number"],
                             student["student_name"], fee["category"],
                             fee["total_amount"], discount, fee["paid"],
                             fee["balance"], fee["status"],
                             _date(fee["due_date"]), fee["remarks"])
                        )

    grand = report.get("totals", {})
    _table(
        sheet,
        start_row=row,
        headers=["Program", "Year level", "Set", "Students", "Billed",
                 "Discounts", "Paid", "Balance", "Rate"],
        rows=summary_rows,
        widths=[38, 10, 8, 9, 14, 13, 14, 14, 9],
        formats={4: MONEY, 5: MONEY, 6: MONEY, 7: MONEY, 8: PERCENT},
        styler=lambda r: {
            5: BAD if r[5] else None,
            7: _zero_balance_style(r[7], r[4]),
            8: _rate_style(r[8]),
        },
        total_row=("All programs", None, None, grand.get("student_count", 0),
                   grand.get("billed", 0), grand.get("discounts", 0),
                   grand.get("paid", 0), grand.get("balance", 0),
                   grand.get("collection_rate", 0)),
    )

    _data_sheet(
        workbook,
        "Students",
        headers=["Program", "Year", "Set", "Student No", "Student", "Billed",
                 "Discounts", "Paid", "Balance", "Rate"],
        rows=student_rows,
        widths=[38, 6, 6, 13, 26, 13, 13, 13, 13, 9],
        formats={5: MONEY, 6: MONEY, 7: MONEY, 8: MONEY, 9: PERCENT},
        styler=lambda r: {
            6: BAD if r[6] else None,
            8: _zero_balance_style(r[8], r[5]),
            9: _rate_style(r[9]),
        },
        total_row=(f"Total ({len(student_rows)} students)", None, None, None,
                   None, grand.get("billed", 0), grand.get("discounts", 0),
                   grand.get("paid", 0), grand.get("balance", 0),
                   grand.get("collection_rate", 0)),
    )
    _data_sheet(
        workbook,
        "Fees",
        headers=["Program", "Year", "Set", "Student No", "Student", "Category",
                 "Amount", "Discount", "Paid", "Balance", "Status", "Due",
                 "Remarks"],
        rows=fee_rows,
        widths=[38, 6, 6, 13, 26, 22, 12, 12, 12, 12, 10, 11, 28],
        formats={6: MONEY, 7: MONEY, 8: MONEY, 9: MONEY},
        styler=lambda r: {
            7: BAD if r[7] else None,
            9: _zero_balance_style(r[9], r[6]),
            10: _status_style(r[10], FEE_STATUS_STYLES),
        },
        total_row=(f"Total ({len(fee_rows)} fees)", None, None, None, None,
                   None, round(fee_amount, 2), round(fee_discount, 2),
                   round(fee_paid, 2), round(fee_balance, 2), None, None,
                   None),
    )

    return _finish(workbook)
