"""
Shaping shared by every report service.

Everything here is arithmetic and formatting - no queries. The rules live in
one place so the collection report and the detailed reports cannot drift on
questions like "how is a rate rounded" or "does a waived fee count".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional


def money(value: Decimal | float | None) -> float:
    """Money as a plain number, two decimals. None reads as zero."""
    return round(float(value or 0), 2)


def rate(part: Decimal | float | None, whole: Decimal | float | None) -> float:
    """
    `part` as a percentage of `whole` - `85.5`, not `0.855`.

    Nothing expected means nothing outstanding, but reporting 100% for an
    empty group would put it top of every "best performing" list, so it
    reads as 0.0 instead.
    """
    whole = float(whole or 0)
    if whole <= 0:
        return 0.0
    return round(float(part or 0) / whole * 100, 1)


def full_name(first, middle, last, suffix=None) -> str:
    parts = [
        (first or "").strip(),
        (middle or "").strip(),
        (last or "").strip(),
        (suffix or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def student_name(student) -> str:
    return full_name(
        student.s_fname, student.s_mname, student.s_lname, student.s_suffix
    )


def export_filename(*, prefix: str, extension: str, now=None) -> str:
    """`collection-report-20260816-0233.pdf` - any report, any format."""
    from django.utils import timezone

    from ..constants import EXPORT_FILENAME, EXPORT_STAMP_FORMAT

    stamp = timezone.localtime(now or timezone.now()).strftime(EXPORT_STAMP_FORMAT)
    return EXPORT_FILENAME.format(prefix=prefix, stamp=stamp, extension=extension)
