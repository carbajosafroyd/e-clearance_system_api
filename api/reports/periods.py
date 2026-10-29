"""
What window is being reported on - and what came immediately before it.

Two ways of naming a period reach this endpoint and they do not mix: an
explicit date range, or an academic year with an optional semester. A date
range always wins, which is the same rule the report sidebar follows.

No database access here. Everything is a value computed from the query
parameters, which makes the previous-period rules testable on their own.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any, Optional

from core.choices import Semester

DISPLAY_DATE = "%d %b %Y"


@dataclass(frozen=True)
class ReportFilters:
    """Everything the caller asked for, normalised.

    Blank strings never survive: the frontend strips empty params before
    sending, and anything that slips through is turned into None here so the
    selectors can treat "absent" as "no filter" without re-checking for "".
    """

    academic_year: Optional[str] = None
    semester: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    program_name: Optional[str] = None
    s_set: Optional[str] = None
    year_level: Optional[int] = None
    compare_previous: bool = False
    #: The optional-module reports narrow on these two as well: which system a
    #: complaint or contribution was filed against, and which status(es) to
    #: keep. Neither means anything to the money reports, which ignore them.
    system: Optional[str] = None
    statuses: tuple[str, ...] = ()

    @classmethod
    def from_validated_data(cls, data: dict[str, Any]) -> "ReportFilters":
        raw_status = data.get("status") or ""
        return cls(
            academic_year=data.get("academic_year") or None,
            semester=data.get("semester") or None,
            date_from=data.get("date_from"),
            date_to=data.get("date_to"),
            # Dunder name because that is what the frontend sends - it mirrors
            # the ORM lookup used on the fee list endpoint.
            program_name=data.get("program__name") or None,
            s_set=data.get("s_set") or None,
            year_level=data.get("year_level"),
            compare_previous=bool(data.get("compare_previous")),
            system=data.get("system") or None,
            # Comma-separated on the wire, so one query string can ask for
            # "open" as `submitted,acknowledged` without repeating the key.
            statuses=tuple(
                part.strip() for part in raw_status.split(",") if part.strip()
            ),
        )

    @property
    def uses_date_range(self) -> bool:
        """A date range overrides academic year and semester entirely."""
        return bool(self.date_from or self.date_to)

    @property
    def label(self) -> str:
        """Human-readable period. Used for the PDF header, nothing else."""
        if self.uses_date_range:
            start = self.date_from.strftime(DISPLAY_DATE) if self.date_from else None
            end = self.date_to.strftime(DISPLAY_DATE) if self.date_to else None
            if start and end:
                return f"{start} - {end}"
            return f"From {start}" if start else f"Up to {end}"

        if self.academic_year and self.semester:
            return f"{Semester(self.semester).label}, A.Y. {self.academic_year}"
        if self.academic_year:
            return f"A.Y. {self.academic_year}"
        if self.semester:
            # str() because the choice label is a lazy translation object.
            return str(Semester(self.semester).label)
        return "All time"


def previous_period(
    filters: ReportFilters, *, today: date
) -> Optional[ReportFilters]:
    """
    The same query over the window immediately before this one.

    Returns None when there is nothing sensible to compare against, and the
    caller reports `previous_total_collections: null` rather than inventing a
    baseline of zero.
    """
    if filters.uses_date_range:
        start = filters.date_from
        # An open end just runs to today; an open *start* has no length, so
        # there is no "same amount of time before it" to compare with.
        end = filters.date_to or today
        if start is None or end < start:
            return None

        length = (end - start).days + 1
        return replace(
            filters,
            date_from=start - timedelta(days=length),
            date_to=start - timedelta(days=1),
        )

    if filters.academic_year and filters.semester:
        # 2nd semester compares against the 1st of the same year; the 1st
        # compares against the 2nd of the year before.
        if filters.semester == Semester.SECOND:
            return replace(filters, semester=Semester.FIRST)

        earlier = shift_academic_year(filters.academic_year, -1)
        if earlier is None:
            return None
        return replace(filters, academic_year=earlier, semester=Semester.SECOND)

    if filters.academic_year:
        earlier = shift_academic_year(filters.academic_year, -1)
        return replace(filters, academic_year=earlier) if earlier else None

    # A semester on its own says nothing about which year to step back into,
    # and an empty filter set has no "before" at all.
    return None


def shift_academic_year(academic_year: str, offset: int) -> Optional[str]:
    """`"2025-2026"` shifted by -1 is `"2024-2025"`. None if unparseable."""
    try:
        start, end = academic_year.split("-")
        return f"{int(start) + offset}-{int(end) + offset}"
    except (ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------
# Months
# --------------------------------------------------------------------------
# The trend and payment-method series are cut by calendar month, which is
# where the database's TruncMonth cuts. Everything here works in plain dates.


def month_start(day: date) -> date:
    """The first of that day's month."""
    return day.replace(day=1)


def month_end(day: date) -> date:
    """The last day of that day's month."""
    return date(day.year, day.month, monthrange(day.year, day.month)[1])


def month_count(start: date, end: date) -> int:
    """How many months the span touches, counting part months at both ends."""
    if end < start:
        return 0
    return (end.year - start.year) * 12 + (end.month - start.month) + 1


def months_between(start: date, end: date) -> list[date]:
    """The first of every month from `start`'s through `end`'s, inclusive."""
    months = []
    current = month_start(start)
    last = month_start(end)
    while current <= last:
        months.append(current)
        # Step to the first of the next month without any day-of-month
        # arithmetic to get wrong.
        current = (current + timedelta(days=32)).replace(day=1)
    return months


def month_label(start: date) -> str:
    """`"2026-02"` - year and month, and it sorts as a string."""
    from .constants import MONTH_LABEL

    return MONTH_LABEL.format(start=start)
