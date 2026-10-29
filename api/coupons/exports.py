"""
Getting a batch out of the system and onto paper.

Both formats are built from the same rows, so the spreadsheet and the
printed sheet can never disagree about what a coupon is worth or when it
dies.

Every export carries the same six facts, because a coupon in somebody's hand
has to answer them without the system: what the code is, whose it is, what it
takes off, what it applies to, how many uses are left, and when it stops
working.
"""

from __future__ import annotations

import io
from typing import Any

import segno
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from django.template.loader import render_to_string
from django.utils import timezone
from xhtml2pdf import pisa

from .models import Coupon, CouponBatch
from .utils import claim_link


HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
THIN = Side(style="thin", color="D1D5DB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

COLUMNS = [
    ("Code", 20),
    ("Student ID", 16),
    ("Student", 26),
    ("Value", 20),
    ("Applies to", 26),
    ("Uses", 9),
    ("Valid until", 14),
    ("Status", 15),
]


def _rows(batch: CouponBatch) -> list[Coupon]:
    return list(
        batch.coupons.select_related("student").order_by("student__s_lname", "id")
    )


def _applies_to(batch: CouponBatch) -> str:
    names = [c.category_name for c in batch.categories.all()]
    return ", ".join(names) if names else "Any fee"


def _row_values(coupon: Coupon, batch: CouponBatch) -> list[Any]:
    student = coupon.student
    return [
        coupon.code,
        student.s_studentID if student else "—",
        f"{student.s_fname} {student.s_lname}".strip() if student else "Bearer",
        batch.describe_value(),
        _applies_to(batch),
        f"{coupon.uses_taken}/{coupon.max_redemptions}",
        timezone.localtime(batch.expires_at).strftime("%d %b %Y"),
        coupon.status_label,
    ]


def to_xlsx(batch: CouponBatch) -> bytes:
    """One row per coupon, with the batch's own terms in a header block."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Coupons"

    # The header block answers "what was this run?" for somebody opening the
    # file a year later with no memory of it.
    meta = [
        ("Batch", batch.name),
        ("Reason", batch.reason or "—"),
        ("Value", batch.describe_value()),
        ("Applies to", _applies_to(batch)),
        ("Term", batch.describe_term()),
        ("Valid", f"{timezone.localtime(batch.valid_from):%d %b %Y}"
                  f" – {timezone.localtime(batch.expires_at):%d %b %Y}"),
        ("Issued by", batch.issued_by.username if batch.issued_by else "—"),
        ("Issued on", f"{timezone.localtime(batch.created_at):%d %b %Y}"),
        ("Coupons", batch.total_created),
        ("Status", batch.get_status_display()),
    ]
    for index, (label, value) in enumerate(meta, start=1):
        sheet.cell(row=index, column=1, value=label).font = Font(bold=True)
        sheet.cell(row=index, column=2, value=value)

    start = len(meta) + 2
    for column, (title, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=start, column=column, value=title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        cell.border = BORDER
        sheet.column_dimensions[get_column_letter(column)].width = width

    for offset, coupon in enumerate(_rows(batch), start=1):
        for column, value in enumerate(_row_values(coupon, batch), start=1):
            cell = sheet.cell(row=start + offset, column=column, value=value)
            cell.border = BORDER

    sheet.freeze_panes = sheet.cell(row=start + 1, column=1)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _qr_data_uri(payload: str) -> str:
    """
    A QR as an inline PNG.

    Data URI rather than a file: xhtml2pdf resolves relative paths against
    the filesystem, and a print run should not depend on where the process
    happened to be running from.
    """
    buffer = io.BytesIO()
    # scale=6 rather than 3: the sheet prints these at roughly 2.9cm, and a
    # bitmap generated for 1.5cm was being upscaled into something a phone
    # camera had to be held still for. A QR PNG is a couple of kilobytes, so
    # the extra source resolution costs nothing worth counting even on a run
    # of several hundred.
    segno.make(payload, error="m").save(buffer, kind="png", scale=6, border=1)
    import base64

    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def to_pdf(batch: CouponBatch) -> bytes:
    """
    A sheet of tickets to cut up.

    Two columns of dashed-border cards, the code in a monospace face big
    enough to read across a table, and a QR of the same code so the holder
    can scan instead of typing it.
    """
    coupons = _rows(batch)
    tickets = [
        {
            "code": coupon.code,
            "student": (
                f"{coupon.student.s_fname} {coupon.student.s_lname}".strip()
                if coupon.student
                else None
            ),
            "student_id": coupon.student.s_studentID if coupon.student else None,
            "qr": _qr_data_uri(claim_link(coupon.code)),
        }
        for coupon in coupons
    ]

    html = render_to_string(
        "coupons/print_sheet.html",
        {
            "batch": batch,
            "tickets": tickets,
            "value_label": batch.describe_value(),
            "applies_to": _applies_to(batch),
            "term_label": batch.describe_term(),
            "expires": timezone.localtime(batch.expires_at),
            "generated": timezone.localtime(timezone.now()),
        },
    )

    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer, encoding="utf-8")
    if result.err:
        raise RuntimeError("Could not render the coupon sheet.")
    return buffer.getvalue()
