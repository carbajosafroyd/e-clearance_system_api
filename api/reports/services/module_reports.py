"""
Assembly of the optional-module reports.

Five reports, one builder each, every one served as JSON, PDF and Excel from
the same snapshot - the same discipline as the collection report, and for
the same reason: the number on paper and the number on screen come from one
code path.

    complaints       what students said we got wrong, and how it was answered
    contributions    the reports & ideas queue, and what it paid out
    leaderboard      the standing, by points earned
    point_discounts  points that became pesos off a fee - the cost of the scheme
    coupons          every batch, every code, every redemption

Each snapshot carries the same three header facts - `institute`, `school`,
`prepared_by` - and a `summary` block, so a coordinator reading the PDF a
year later has the totals without re-adding the rows.

Everything below is arithmetic over what `module_selectors.py` returned.
No queries live here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Optional

from django.utils import timezone

from api.complaints.models import Complaint, ComplaintCategory
from api.contributions.models import (
    Contribution,
    ContributionImpact,
    ContributionKind,
    FeeDiscount,
)
from api.coupons.models import Coupon, CouponBatch, CouponRedemption
from core.choices import SystemScope

from .. import module_selectors as selectors
from ..periods import ReportFilters
from .common import full_name, institute_scope, money, rate

SYSTEM_LABELS = dict(SystemScope.choices)


# --------------------------------------------------------------------------
# Shared shaping
# --------------------------------------------------------------------------


def _header(*, user, filters: ReportFilters) -> dict[str, Any]:
    """The facts every module report prints above its tables."""
    institute = getattr(user, "institute", None)
    school = getattr(institute, "school", None) if institute else None
    return {
        "generated_at": timezone.now().isoformat(),
        "institute": getattr(institute, "institute_name", None),
        "institute_acronym": getattr(institute, "acronym", None) or None,
        "school": getattr(school, "school_name", None),
        "prepared_by": user.get_username(),
        "period": filters.label,
        "filters": {
            "system": filters.system,
            "system_label": SYSTEM_LABELS.get(filters.system, filters.system)
            if filters.system
            else None,
            "statuses": list(filters.statuses),
            "program": filters.program_name,
            "set": filters.s_set,
            "year_level": filters.year_level,
        },
    }


def _student(row_or_obj, prefix: str = "student__") -> dict[str, Any]:
    """A student as a report row names them - from a values() row or an object."""
    if isinstance(row_or_obj, dict):
        get = lambda key: row_or_obj.get(f"{prefix}{key}")  # noqa: E731
        return {
            "student_number": get("s_studentID"),
            "student_name": full_name(
                get("s_fname"), get("s_mname"), get("s_lname"), get("s_suffix")
            ),
            "program": get("program__name") or "—",
            "program_acronym": get("program__acronym") or None,
            "year_level": get("s_lvl"),
            "set": get("s_set") or "—",
        }
    student = row_or_obj
    if student is None:
        return {
            "student_number": None,
            "student_name": None,
            "program": None,
            "program_acronym": None,
            "year_level": None,
            "set": None,
        }
    program = getattr(student, "program", None)
    return {
        "student_number": student.s_studentID,
        "student_name": full_name(
            student.s_fname, student.s_mname, student.s_lname, student.s_suffix
        ),
        "program": getattr(program, "name", None) or "—",
        "program_acronym": getattr(program, "acronym", None) or None,
        "year_level": student.s_lvl,
        "set": student.s_set or "—",
    }


def _iso(value: Optional[datetime | date]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return timezone.localtime(value).isoformat() if timezone.is_aware(value) else value.isoformat()
    return value.isoformat()


def _username(user) -> Optional[str]:
    return user.get_username() if user is not None else None


def _days_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 86400, 1)


def _by_system(rows: Iterable[dict[str, Any]], key: str, zero: dict[str, Any]) -> list[dict[str, Any]]:
    """
    A per-system breakdown, zero-filled for every system so a system nobody
    filed against still appears - the difference between "nobody is unhappy"
    and "we forgot to include it".
    """
    found = {row[key]: row for row in rows}
    out = []
    for value, label in SystemScope.choices:
        row = found.get(value, {})
        out.append({"system": value, "label": label, **{k: row.get(k, v) for k, v in zero.items()}})
    return out


# --------------------------------------------------------------------------
# Complaints
# --------------------------------------------------------------------------


def build_complaints_report(*, user, filters: ReportFilters) -> dict[str, Any]:
    institute_id = institute_scope(user)
    complaints = selectors.complaint_queryset(filters=filters, institute_id=institute_id)

    counts = selectors.complaint_counts(complaints)
    status_labels = dict(Complaint.Status.choices)
    category_labels = dict(ComplaintCategory.choices)

    records = []
    close_days: list[float] = []
    for row in complaints:
        days = _days_between(row.created_at, row.handled_at)
        if row.status in (Complaint.Status.RESOLVED, Complaint.Status.REJECTED) and days is not None:
            close_days.append(days)
        fee = row.related_fee
        records.append(
            {
                "id": row.pk,
                **_student(row.student),
                "system": row.system,
                "system_label": SYSTEM_LABELS.get(row.system, row.system),
                "category": row.category,
                "category_label": category_labels.get(row.category, row.category),
                "title": row.title,
                "description": row.description,
                "status": row.status,
                "status_label": status_labels.get(row.status, row.status),
                "fee_id": fee.pk if fee else None,
                "fee_category": fee.category.category_name if fee else None,
                "fee_amount": money(fee.total_amount) if fee else None,
                "fee_balance": money(fee.balance) if fee else None,
                "fee_status": fee.status if fee else None,
                "filed_at": _iso(row.created_at),
                "handled_by": _username(row.handled_by),
                "handled_at": _iso(row.handled_at),
                "days_open": days
                if days is not None
                else _days_between(row.created_at, timezone.now()),
                "response": row.response or None,
            }
        )

    by_system = _by_system(
        selectors.complaint_rows_by(complaints, "system"),
        "system",
        {"total": 0, "open": 0, "resolved": 0, "rejected": 0, "withdrawn": 0},
    )
    by_category = [
        {
            "category": row["category"],
            "label": category_labels.get(row["category"], row["category"]),
            "total": row["total"],
            "open": row["open"],
            "resolved": row["resolved"],
            "rejected": row["rejected"],
            "withdrawn": row["withdrawn"],
        }
        for row in selectors.complaint_rows_by(complaints, "category")
    ]

    decided = counts["resolved"] + counts["rejected"]
    return {
        **_header(user=user, filters=filters),
        "summary": {
            "total": counts["total"],
            "open": counts["submitted"] + counts["acknowledged"],
            "submitted": counts["submitted"],
            "acknowledged": counts["acknowledged"],
            "resolved": counts["resolved"],
            "rejected": counts["rejected"],
            "withdrawn": counts["withdrawn"],
            "fee_related": counts["fee_related"],
            # Of the ones somebody answered, how many were resolved rather
            # than closed without action. Null until something was decided.
            "resolution_rate": rate(counts["resolved"], decided) if decided else None,
            "average_days_to_close": (
                round(sum(close_days) / len(close_days), 1) if close_days else None
            ),
        },
        "by_system": by_system,
        "by_category": by_category,
        "records": records,
    }


# --------------------------------------------------------------------------
# Reports & ideas (contributions)
# --------------------------------------------------------------------------


def build_contributions_report(*, user, filters: ReportFilters) -> dict[str, Any]:
    institute_id = institute_scope(user)
    contributions = selectors.contribution_queryset(
        filters=filters, institute_id=institute_id
    )
    counts = selectors.contribution_counts(contributions)
    status_labels = dict(Contribution.Status.choices)
    kind_labels = dict(ContributionKind.choices)
    impact_labels = dict(ContributionImpact.choices)

    records = [
        {
            "id": row.pk,
            **_student(row.student),
            "system": row.system,
            "system_label": SYSTEM_LABELS.get(row.system, row.system),
            "kind": row.kind,
            "kind_label": kind_labels.get(row.kind, row.kind),
            "impact": row.impact,
            "impact_label": impact_labels.get(row.impact, row.impact),
            "title": row.title,
            "description": row.description,
            "status": row.status,
            "status_label": status_labels.get(row.status, row.status),
            "points_awarded": row.points_awarded,
            "filed_at": _iso(row.created_at),
            "reviewed_by": _username(row.reviewed_by),
            "reviewed_at": _iso(row.reviewed_at),
            "review_remarks": row.review_remarks or None,
        }
        for row in contributions
    ]

    by_system = _by_system(
        selectors.contribution_rows_by(contributions, "system"),
        "system",
        {"total": 0, "approved": 0, "pending": 0, "rejected": 0, "points": 0},
    )
    by_kind = [
        {
            "kind": row["kind"],
            "label": kind_labels.get(row["kind"], row["kind"]),
            "total": row["total"],
            "approved": row["approved"],
            "pending": row["pending"],
            "rejected": row["rejected"],
            "points": row["points"],
        }
        for row in selectors.contribution_rows_by(contributions, "kind")
    ]
    by_impact = [
        {
            "impact": row["impact"],
            "label": impact_labels.get(row["impact"], row["impact"]),
            "total": row["total"],
            "approved": row["approved"],
            "points": row["points"],
        }
        for row in selectors.contribution_rows_by(contributions, "impact")
    ]

    decided = counts["approved"] + counts["rejected"] + counts["duplicate"]
    return {
        **_header(user=user, filters=filters),
        "summary": {
            "total": counts["total"],
            "pending": counts["pending"],
            "under_review": counts["under_review"],
            "approved": counts["approved"],
            "rejected": counts["rejected"],
            "duplicate": counts["duplicate"],
            "points_awarded": counts["points_awarded"],
            "students": counts["students"],
            # Approved over everything decided; pending and under-review sit
            # outside it, so a backlog does not read as a low approval rate.
            "approval_rate": rate(counts["approved"], decided) if decided else None,
        },
        "by_system": by_system,
        "by_kind": by_kind,
        "by_impact": by_impact,
        "records": records,
    }


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------


def build_leaderboard_report(*, user, filters: ReportFilters) -> dict[str, Any]:
    institute_id = institute_scope(user)
    accounts = selectors.point_account_queryset(filters=filters, institute_id=institute_id)
    rows = selectors.leaderboard_rows(accounts)
    approved = selectors.approved_contribution_counts(
        [row["student_id"] for row in rows], system_name=filters.system
    )

    # Competition ranking: equal points share a place, and the next place
    # skips the tie - so two students on 90 points are both 1st and the
    # student on 80 is 3rd, the same way the wallet page counts it.
    ranked = []
    previous_points = None
    place = 0
    for index, row in enumerate(rows, start=1):
        if row["points_earned"] != previous_points:
            place = index
            previous_points = row["points_earned"]
        ranked.append(
            {
                "rank": place,
                **_student(row),
                "points_earned": row["points_earned"],
                "points_spent": row["points_spent"],
                "points_balance": row["points_balance"],
                "approved_contributions": approved.get(row["student_id"], 0),
            }
        )

    by_system = _by_system(
        selectors.wallet_totals_by_system(accounts),
        "system",
        {"students": 0, "points_earned": 0, "points_spent": 0, "points_balance": 0},
    )

    return {
        **_header(user=user, filters=filters),
        "system": filters.system or "overall",
        "system_label": SYSTEM_LABELS.get(filters.system, "All systems")
        if filters.system
        else "All systems",
        "summary": {
            "students": len(ranked),
            "points_earned": sum(row["points_earned"] for row in ranked),
            "points_spent": sum(row["points_spent"] for row in ranked),
            "points_balance": sum(row["points_balance"] for row in ranked),
            "approved_contributions": sum(row["approved_contributions"] for row in ranked),
        },
        "by_system": by_system,
        "rows": ranked,
    }


# --------------------------------------------------------------------------
# Point discounts
# --------------------------------------------------------------------------


def build_point_discounts_report(*, user, filters: ReportFilters) -> dict[str, Any]:
    institute_id = institute_scope(user)
    discounts = selectors.fee_discount_queryset(filters=filters, institute_id=institute_id)
    totals = selectors.fee_discount_totals(discounts)
    status_labels = dict(FeeDiscount.Status.choices)

    records = [
        {
            "id": row.pk,
            **_student(row.student),
            "system": row.system,
            "system_label": SYSTEM_LABELS.get(row.system, row.system),
            "fee_id": row.fee_id,
            "fee_category": row.fee.category.category_name,
            "fee_term": f"{row.fee.semester} {row.fee.academic_year}",
            "fee_billed": money(row.fee.total_amount),
            "fee_balance": money(row.fee.balance),
            "fee_status": row.fee.status,
            "points_spent": row.points_spent,
            "amount": money(row.amount),
            "status": row.status,
            "status_label": status_labels.get(row.status, row.status),
            "requested_at": _iso(row.created_at),
            "requested_by": _username(row.requested_by),
            "reviewed_by": _username(row.reviewed_by),
            "reviewed_at": _iso(row.reviewed_at),
            "remarks": row.remarks or None,
            "cancelled_by": _username(row.cancelled_by),
            "cancelled_at": _iso(row.cancelled_at),
            "cancellation_reason": row.cancellation_reason or None,
        }
        for row in discounts
    ]

    by_system = _by_system(
        [
            {**row, "amount": money(row["amount"])}
            for row in selectors.fee_discount_rows_by(discounts, "system")
        ],
        "system",
        {"requests": 0, "approved": 0, "amount": 0.0, "points": 0},
    )
    by_category = [
        {
            "category": row["fee__category__category_name"],
            "requests": row["requests"],
            "approved": row["approved"],
            "amount": money(row["amount"]),
            "points": row["points"],
        }
        for row in selectors.fee_discount_rows_by(discounts, "fee__category__category_name")
    ]

    return {
        **_header(user=user, filters=filters),
        "summary": {
            "requests": totals["requests"],
            "pending": totals["pending"],
            "approved": totals["approved"],
            "rejected": totals["rejected"],
            "cancelled": totals["cancelled"],
            "points_spent": totals["points_applied"],
            "points_pending": totals["points_on_hold"],
            # The cost of the scheme: relief that is on fees right now.
            "amount_approved": money(totals["amount_approved"]),
            "amount_pending": money(totals["amount_pending"]),
            "amount_cancelled": money(totals["amount_cancelled"]),
            "students_helped": totals["students_helped"],
        },
        "by_system": by_system,
        "by_category": by_category,
        "records": records,
    }


# --------------------------------------------------------------------------
# Coupons
# --------------------------------------------------------------------------


def build_coupons_report(*, user, filters: ReportFilters) -> dict[str, Any]:
    institute_id = institute_scope(user)
    batches = selectors.coupon_batch_queryset(filters=filters, institute_id=institute_id)
    coupons = selectors.coupon_queryset(batches)
    redemptions = selectors.coupon_redemption_queryset(
        batches, filters=filters, institute_id=institute_id
    )
    coupon_counts = selectors.coupon_counts(coupons)
    redemption_totals = selectors.redemption_totals(redemptions)
    per_batch = selectors.redemption_totals_by_batch(redemptions)

    batch_status_labels = dict(CouponBatch.Status.choices)
    kind_labels = dict(CouponBatch.Kind.choices)
    coupon_status_labels = dict(Coupon.Status.choices)
    redemption_status_labels = dict(CouponRedemption.Status.choices)

    coupon_rows = list(coupons)

    # `Coupon.is_spent` is a query per coupon; the redemptions are already
    # prefetched, so the same answer comes from memory - a batch of two
    # thousand codes must not cost two thousand queries.
    def spent(coupon) -> bool:
        return any(
            r.status == CouponRedemption.Status.APPLIED for r in coupon.redemptions.all()
        )

    def label(coupon) -> str:
        # Mirrors `Coupon.status_label`: a held coupon's column says
        # fully_redeemed, which is true of its capacity and a lie about the
        # money, so it prints as Claimed.
        if coupon.status == Coupon.Status.FULLY_REDEEMED and not spent(coupon):
            return "Claimed"
        return coupon_status_labels.get(coupon.status, coupon.status)

    per_batch_coupons: dict[int, dict[str, int]] = {}
    for coupon in coupon_rows:
        bucket = per_batch_coupons.setdefault(
            coupon.batch_id, {"issued": 0, "revoked": 0, "expired": 0, "spent": 0}
        )
        bucket["issued"] += 1
        if coupon.status == Coupon.Status.REVOKED:
            bucket["revoked"] += 1
        elif coupon.status == Coupon.Status.EXPIRED:
            bucket["expired"] += 1
        if spent(coupon):
            bucket["spent"] += 1

    batch_records = []
    liability = 0.0
    for batch in batches:
        counts = per_batch_coupons.get(batch.pk, {})
        moved = per_batch.get(batch.pk, {})
        issued = counts.get("issued", 0)
        spent_count = counts.get("spent", 0)
        revoked = counts.get("revoked", 0)
        expired = counts.get("expired", 0)
        unspent = max(0, issued - spent_count - revoked - expired)
        # What this batch could still cost: every live, unspent coupon at
        # face value. Only a fixed amount has a face value; a percentage
        # without a ceiling is an open cheque and reads as unknown.
        if batch.value_type == CouponBatch.ValueType.FIXED:
            outstanding = float(batch.value) * unspent
        elif batch.max_discount is not None:
            outstanding = float(batch.max_discount) * unspent
        else:
            outstanding = None
        if outstanding is not None and batch.status == CouponBatch.Status.ACTIVE:
            liability += outstanding
        batch_records.append(
            {
                "id": batch.pk,
                "name": batch.name,
                "reason": batch.reason or None,
                "kind": batch.kind,
                "kind_label": kind_labels.get(batch.kind, batch.kind),
                "value_label": batch.describe_value(),
                "applies_to": ", ".join(
                    category.category_name for category in batch.categories.all()
                )
                or "Any fee",
                "term_label": batch.describe_term(),
                "valid_from": _iso(batch.valid_from),
                "expires_at": _iso(batch.expires_at),
                "one_per_student": batch.one_per_student,
                "status": batch.status,
                "status_label": batch_status_labels.get(batch.status, batch.status),
                "issued_by": _username(batch.issued_by),
                "issued_at": _iso(batch.created_at),
                "revoked_by": _username(batch.revoked_by),
                "revoked_at": _iso(batch.revoked_at),
                "revocation_reason": batch.revocation_reason or None,
                "issued": issued,
                "claimed": moved.get("claimed", 0),
                "applied": moved.get("applied", 0),
                "reversed": moved.get("reversed", 0),
                "revoked": revoked,
                "expired": expired,
                "unspent": unspent,
                "amount_applied": money(moved.get("amount_applied")),
                "outstanding_value": money(outstanding) if outstanding is not None else None,
            }
        )

    coupon_records = [
        {
            "id": coupon.pk,
            "code": coupon.code,
            "batch_id": coupon.batch_id,
            "batch": coupon.batch.name,
            "value_label": coupon.batch.describe_value(),
            "bound": coupon.student_id is not None,
            **_student(coupon.student),
            "max_redemptions": coupon.max_redemptions,
            "uses_taken": coupon.uses_taken,
            "is_spent": spent(coupon),
            "status": coupon.status,
            "status_label": label(coupon),
            "expires_at": _iso(coupon.batch.expires_at),
            "revoked_at": _iso(coupon.revoked_at),
            "revocation_reason": coupon.revocation_reason or None,
        }
        for coupon in coupon_rows
    ]

    redemption_records = [
        {
            "id": row.pk,
            "code": row.coupon.code,
            "batch": row.coupon.batch.name,
            **_student(row.student),
            "fee_id": row.fee_id,
            "fee_category": row.fee.category.category_name if row.fee else None,
            "fee_term": f"{row.fee.semester} {row.fee.academic_year}" if row.fee else None,
            "amount": money(row.amount),
            "previous_balance": money(row.previous_balance) if row.previous_balance is not None else None,
            "status": row.status,
            "status_label": redemption_status_labels.get(row.status, row.status),
            "claimed_at": _iso(row.created_at),
            "applied_at": _iso(row.applied_at),
            "applied_by": _username(getattr(row, "updated_by", None)) if row.applied_at else None,
            "reversed_by": _username(row.reversed_by),
            "reversed_at": _iso(row.reversed_at),
            "reversal_reason": row.reversal_reason or None,
        }
        for row in redemptions
    ]

    return {
        **_header(user=user, filters=filters),
        "summary": {
            "batches": len(batch_records),
            "active_batches": sum(1 for b in batch_records if b["status"] == CouponBatch.Status.ACTIVE),
            "issued": coupon_counts["issued"],
            "bound": coupon_counts["bound"],
            "bearer": coupon_counts["bearer"],
            "claimed": redemption_totals["claimed"],
            "applied": redemption_totals["applied"],
            "reversed": redemption_totals["reversed"],
            "revoked": coupon_counts["revoked"],
            "expired": coupon_counts["expired"],
            "students_helped": redemption_totals["students"],
            # Relief that is on fees right now, and relief that was taken back.
            "amount_applied": money(redemption_totals["amount_applied"]),
            "amount_reversed": money(redemption_totals["amount_reversed"]),
            # What the live, unspent coupons could still cost at face value.
            "outstanding_value": money(liability),
        },
        "batches": batch_records,
        "coupons": coupon_records,
        "redemptions": redemption_records,
    }
