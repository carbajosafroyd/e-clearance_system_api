"""
Read-side queries for complaints.

Nothing here writes, so it is safe to call from anywhere, and the aggregate
shapes stay in one place instead of being re-derived per view.
"""

from __future__ import annotations

from typing import Any, Optional

from django.db.models import Count, Q

from core.choices import SystemScope

from .models import Complaint, ComplaintCategory


def _scoped(queryset, *, institute_id: Optional[int]):
    if institute_id is None:
        return queryset
    return queryset.filter(student__program__institute_id=institute_id)


def system_breakdown(
    *, institute_id: Optional[int] = None
) -> list[dict[str, Any]]:
    """One row per system, zero-filled.

    A system with no complaints is a fact worth showing - it is the
    difference between "nobody is unhappy" and "we forgot to include it".
    """
    counted = {
        row["system"]: row
        for row in _scoped(Complaint.objects.all(), institute_id=institute_id)
        .values("system")
        .annotate(
            total=Count("id"),
            open=Count("id", filter=Q(status__in=Complaint.OPEN_STATUSES)),
            resolved=Count("id", filter=Q(status=Complaint.Status.RESOLVED)),
        )
    }

    return [
        {
            "system": value,
            "label": label,
            "total": counted.get(value, {}).get("total", 0),
            "open": counted.get(value, {}).get("open", 0),
            "resolved": counted.get(value, {}).get("resolved", 0),
        }
        for value, label in SystemScope.choices
    ]


def category_breakdown(
    *, institute_id: Optional[int] = None, system: Optional[str] = None
) -> list[dict[str, Any]]:
    """What students actually complain about, commonest first."""
    queryset = _scoped(Complaint.objects.all(), institute_id=institute_id)
    if system:
        queryset = queryset.filter(system=system)

    counted = {
        row["category"]: row["total"]
        for row in queryset.values("category").annotate(total=Count("id"))
    }

    rows = [
        {"category": value, "label": label, "total": counted.get(value, 0)}
        for value, label in ComplaintCategory.choices
    ]
    rows.sort(key=lambda row: row["total"], reverse=True)
    return rows


def complaint_summary(
    *, institute_id: Optional[int] = None, system: Optional[str] = None
) -> dict[str, Any]:
    """Counts for the complaints dashboard."""
    queryset = _scoped(Complaint.objects.all(), institute_id=institute_id)
    if system:
        queryset = queryset.filter(system=system)

    counts = queryset.aggregate(
        total=Count("id"),
        submitted=Count("id", filter=Q(status=Complaint.Status.SUBMITTED)),
        acknowledged=Count("id", filter=Q(status=Complaint.Status.ACKNOWLEDGED)),
        resolved=Count("id", filter=Q(status=Complaint.Status.RESOLVED)),
        rejected=Count("id", filter=Q(status=Complaint.Status.REJECTED)),
        withdrawn=Count("id", filter=Q(status=Complaint.Status.WITHDRAWN)),
    )

    return {
        **counts,
        "open": counts["submitted"] + counts["acknowledged"],
        "by_system": system_breakdown(institute_id=institute_id),
        "by_category": category_breakdown(institute_id=institute_id, system=system),
    }
