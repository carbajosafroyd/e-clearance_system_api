"""
Every database read behind the optional-module reports.

Complaints, reports & ideas, the leaderboard, point discounts and coupons
each get a report in the same three shapes as the money reports (JSON, PDF,
Excel). The rows here are cut the way `selectors.py` cuts fees: by the
caller's institute, by the student-scope filters (program, set, year level),
and by a window - `created_at` for a date range. The academic term applies
only where a record actually carries one, through the fee it sits on.

Everything returns plain rows or aggregates. Shaping belongs to
`services/module_reports.py`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from django.db.models import Count, DecimalField, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce

from api.complaints.models import Complaint
from api.contributions.models import Contribution, FeeDiscount, PointAccount
from api.coupons.models import Coupon, CouponBatch, CouponRedemption

from .periods import ReportFilters

ZERO = Value(
    Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2)
)


def _money(expression, condition=None) -> Coalesce:
    if condition is None:
        return Coalesce(Sum(expression), ZERO)
    return Coalesce(Sum(expression, filter=condition), ZERO)


# --------------------------------------------------------------------------
# Shared narrowing
# --------------------------------------------------------------------------


def scope_students(
    queryset: QuerySet,
    *,
    institute_id: Optional[int],
    filters: ReportFilters,
    path: str = "student",
) -> QuerySet:
    """
    Institute, program, set and year level - through whichever relation
    reaches the student. `institute_id` of None means every institute
    (superuser with no institute of their own); `NO_INSTITUTE` (0) matches
    nothing, which is what a staff account without an institute gets.
    """
    if institute_id is not None:
        queryset = queryset.filter(**{f"{path}__program__institute_id": institute_id})
    if filters.program_name:
        queryset = queryset.filter(**{f"{path}__program__name": filters.program_name})
    if filters.s_set:
        queryset = queryset.filter(**{f"{path}__s_set": filters.s_set})
    if filters.year_level is not None:
        queryset = queryset.filter(**{f"{path}__s_lvl": filters.year_level})
    return queryset


def window(queryset: QuerySet, filters: ReportFilters, field: str = "created_at") -> QuerySet:
    """A record belongs to the window it was created in."""
    if filters.date_from:
        queryset = queryset.filter(**{f"{field}__date__gte": filters.date_from})
    if filters.date_to:
        queryset = queryset.filter(**{f"{field}__date__lte": filters.date_to})
    return queryset


def term(queryset: QuerySet, filters: ReportFilters, path: str) -> QuerySet:
    """Academic year and semester, through the fee the record sits on."""
    if filters.uses_date_range:
        return queryset
    if filters.academic_year:
        queryset = queryset.filter(**{f"{path}__academic_year": filters.academic_year})
    if filters.semester:
        queryset = queryset.filter(**{f"{path}__semester": filters.semester})
    return queryset


def statuses(queryset: QuerySet, filters: ReportFilters, field: str = "status") -> QuerySet:
    if filters.statuses:
        queryset = queryset.filter(**{f"{field}__in": filters.statuses})
    return queryset


def system(queryset: QuerySet, filters: ReportFilters, field: str = "system") -> QuerySet:
    if filters.system:
        queryset = queryset.filter(**{field: filters.system})
    return queryset


# --------------------------------------------------------------------------
# Complaints
# --------------------------------------------------------------------------


def complaint_queryset(
    *, filters: ReportFilters, institute_id: Optional[int]
) -> QuerySet[Complaint]:
    queryset = Complaint.objects.select_related(
        "student__program", "related_fee__category", "handled_by"
    )
    queryset = scope_students(queryset, institute_id=institute_id, filters=filters)
    queryset = window(queryset, filters)
    queryset = system(queryset, filters)
    queryset = statuses(queryset, filters)
    return queryset.order_by("-created_at", "-pk")


def complaint_counts(queryset: QuerySet[Complaint]) -> dict[str, int]:
    status = Complaint.Status
    return queryset.aggregate(
        total=Count("id"),
        submitted=Count("id", filter=Q(status=status.SUBMITTED)),
        acknowledged=Count("id", filter=Q(status=status.ACKNOWLEDGED)),
        resolved=Count("id", filter=Q(status=status.RESOLVED)),
        rejected=Count("id", filter=Q(status=status.REJECTED)),
        withdrawn=Count("id", filter=Q(status=status.WITHDRAWN)),
        fee_related=Count("id", filter=Q(related_fee__isnull=False)),
    )


def complaint_rows_by(queryset: QuerySet[Complaint], field: str) -> list[dict[str, Any]]:
    status = Complaint.Status
    return list(
        queryset.values(field)
        .annotate(
            total=Count("id"),
            open=Count(
                "id",
                filter=Q(status__in=(status.SUBMITTED, status.ACKNOWLEDGED)),
            ),
            resolved=Count("id", filter=Q(status=status.RESOLVED)),
            rejected=Count("id", filter=Q(status=status.REJECTED)),
            withdrawn=Count("id", filter=Q(status=status.WITHDRAWN)),
        )
        .order_by("-total", field)
    )


# --------------------------------------------------------------------------
# Contributions (reports & ideas)
# --------------------------------------------------------------------------


def contribution_queryset(
    *, filters: ReportFilters, institute_id: Optional[int]
) -> QuerySet[Contribution]:
    queryset = Contribution.objects.select_related("student__program", "reviewed_by")
    queryset = scope_students(queryset, institute_id=institute_id, filters=filters)
    queryset = window(queryset, filters)
    queryset = system(queryset, filters)
    queryset = statuses(queryset, filters)
    return queryset.order_by("-created_at", "-pk")


def contribution_counts(queryset: QuerySet[Contribution]) -> dict[str, Any]:
    status = Contribution.Status
    return queryset.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=status.PENDING)),
        under_review=Count("id", filter=Q(status=status.UNDER_REVIEW)),
        approved=Count("id", filter=Q(status=status.APPROVED)),
        rejected=Count("id", filter=Q(status=status.REJECTED)),
        duplicate=Count("id", filter=Q(status=status.DUPLICATE)),
        points_awarded=Coalesce(
            Sum("points_awarded", filter=Q(status=status.APPROVED)), 0
        ),
        students=Count("student", distinct=True),
    )


def contribution_rows_by(
    queryset: QuerySet[Contribution], field: str
) -> list[dict[str, Any]]:
    status = Contribution.Status
    return list(
        queryset.values(field)
        .annotate(
            total=Count("id"),
            approved=Count("id", filter=Q(status=status.APPROVED)),
            pending=Count(
                "id", filter=Q(status__in=(status.PENDING, status.UNDER_REVIEW))
            ),
            rejected=Count(
                "id", filter=Q(status__in=(status.REJECTED, status.DUPLICATE))
            ),
            points=Coalesce(Sum("points_awarded", filter=Q(status=status.APPROVED)), 0),
        )
        .order_by("-total", field)
    )


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------


def point_account_queryset(
    *, filters: ReportFilters, institute_id: Optional[int]
) -> QuerySet[PointAccount]:
    """
    Wallets in scope. The leaderboard has no window - a ranking is a
    standing, not a period - so `date_from` / `date_to` are deliberately
    not applied here; the term and date filters narrow the other reports.
    """
    queryset = PointAccount.objects.select_related("student__program")
    queryset = scope_students(queryset, institute_id=institute_id, filters=filters)
    queryset = system(queryset, filters)
    return queryset


def leaderboard_rows(accounts: QuerySet[PointAccount]) -> list[dict[str, Any]]:
    """
    One row per student, summed across the wallets in scope, ranked by
    points *earned* - spending is what the points are for, and a board that
    punished spending would rank the students who never used the scheme
    above those who did. Only students who earned anything appear.
    """
    rows = (
        accounts.values(
            "student_id",
            "student__s_studentID",
            "student__s_fname",
            "student__s_mname",
            "student__s_lname",
            "student__s_suffix",
            "student__program__name",
            "student__program__acronym",
            "student__s_lvl",
            "student__s_set",
        )
        .annotate(
            points_earned=Coalesce(Sum("total_earned"), 0),
            points_spent=Coalesce(Sum("total_spent"), 0),
            points_balance=Coalesce(Sum("balance"), 0),
        )
        .filter(points_earned__gt=0)
        .order_by("-points_earned", "student__s_lname", "student__s_fname")
    )
    return list(rows)


def approved_contribution_counts(
    student_ids: list[int], *, system_name: Optional[str]
) -> dict[int, int]:
    queryset = Contribution.objects.filter(
        student_id__in=student_ids, status=Contribution.Status.APPROVED
    )
    if system_name:
        queryset = queryset.filter(system=system_name)
    rows = queryset.values("student_id").annotate(n=Count("id")).order_by()
    return {row["student_id"]: row["n"] for row in rows}


def wallet_totals_by_system(accounts: QuerySet[PointAccount]) -> list[dict[str, Any]]:
    return list(
        accounts.values("system")
        .annotate(
            students=Count("student", distinct=True),
            points_earned=Coalesce(Sum("total_earned"), 0),
            points_spent=Coalesce(Sum("total_spent"), 0),
            points_balance=Coalesce(Sum("balance"), 0),
        )
        .order_by("system")
    )


# --------------------------------------------------------------------------
# Point discounts
# --------------------------------------------------------------------------


def fee_discount_queryset(
    *, filters: ReportFilters, institute_id: Optional[int]
) -> QuerySet[FeeDiscount]:
    queryset = FeeDiscount.objects.select_related(
        "student__program", "fee__category", "requested_by", "reviewed_by", "cancelled_by"
    )
    queryset = scope_students(queryset, institute_id=institute_id, filters=filters)
    queryset = window(queryset, filters)
    queryset = term(queryset, filters, "fee")
    queryset = system(queryset, filters)
    queryset = statuses(queryset, filters)
    return queryset.order_by("-created_at", "-pk")


def fee_discount_totals(queryset: QuerySet[FeeDiscount]) -> dict[str, Any]:
    status = FeeDiscount.Status
    return queryset.aggregate(
        requests=Count("id"),
        pending=Count("id", filter=Q(status=status.PENDING)),
        approved=Count("id", filter=Q(status=status.APPROVED)),
        rejected=Count("id", filter=Q(status=status.REJECTED)),
        cancelled=Count("id", filter=Q(status=status.CANCELLED)),
        # Aliases must not collide with the column they sum.
        points_applied=Coalesce(Sum("points_spent", filter=Q(status=status.APPROVED)), 0),
        points_on_hold=Coalesce(Sum("points_spent", filter=Q(status=status.PENDING)), 0),
        amount_approved=_money("amount", Q(status=status.APPROVED)),
        amount_pending=_money("amount", Q(status=status.PENDING)),
        amount_cancelled=_money("amount", Q(status=status.CANCELLED)),
        students_helped=Count("student", filter=Q(status=status.APPROVED), distinct=True),
    )


def fee_discount_rows_by(queryset: QuerySet[FeeDiscount], field: str) -> list[dict[str, Any]]:
    status = FeeDiscount.Status
    return list(
        queryset.values(field)
        .annotate(
            requests=Count("id"),
            approved=Count("id", filter=Q(status=status.APPROVED)),
            amount=_money("amount", Q(status=status.APPROVED)),
            points=Coalesce(Sum("points_spent", filter=Q(status=status.APPROVED)), 0),
        )
        .order_by("-amount", field)
    )


# --------------------------------------------------------------------------
# Coupons
# --------------------------------------------------------------------------


def coupon_batch_queryset(
    *, filters: ReportFilters, institute_id: Optional[int]
) -> QuerySet[CouponBatch]:
    """
    Batches in the window they were issued in. The status filter reads
    the batch's own vocabulary (`active`, `revoked`) here.
    """
    queryset = CouponBatch.objects.select_related("issued_by", "revoked_by").prefetch_related(
        "categories"
    )
    if institute_id is not None:
        queryset = queryset.filter(institute_id=institute_id)
    queryset = window(queryset, filters)
    if not filters.uses_date_range:
        # A batch tied to a term is in that term's report; one issued for
        # any term is in every term's report.
        if filters.academic_year:
            queryset = queryset.filter(
                Q(academic_year=filters.academic_year) | Q(academic_year__isnull=True)
                | Q(academic_year="")
            )
        if filters.semester:
            queryset = queryset.filter(
                Q(semester=filters.semester) | Q(semester__isnull=True) | Q(semester="")
            )
    return queryset.order_by("-created_at", "-pk")


def coupon_queryset(batches: QuerySet[CouponBatch]) -> QuerySet[Coupon]:
    return (
        Coupon.objects.filter(batch__in=batches.values("pk"))
        .select_related("batch", "student__program")
        .prefetch_related("redemptions")
        .order_by("batch_id", "student__s_lname", "student__s_fname", "pk")
    )


def coupon_redemption_queryset(
    batches: QuerySet[CouponBatch], *, filters: ReportFilters, institute_id: Optional[int]
) -> QuerySet[CouponRedemption]:
    """
    Every claim, application and reversal on the batches in scope. The
    student-scope filters apply here too, so "coupons used by BSIT 2A" is
    answerable from one report.
    """
    queryset = CouponRedemption.objects.filter(
        coupon__batch__in=batches.values("pk")
    ).select_related(
        "coupon__batch", "student__program", "fee__category", "reversed_by", "updated_by"
    )
    queryset = scope_students(queryset, institute_id=institute_id, filters=filters)
    return queryset.order_by("-created_at", "-pk")


def coupon_counts(coupons: QuerySet[Coupon]) -> dict[str, int]:
    status = Coupon.Status
    return coupons.aggregate(
        issued=Count("id"),
        bound=Count("id", filter=Q(student__isnull=False)),
        bearer=Count("id", filter=Q(student__isnull=True)),
        revoked=Count("id", filter=Q(status=status.REVOKED)),
        expired=Count("id", filter=Q(status=status.EXPIRED)),
    )


def redemption_totals(redemptions: QuerySet[CouponRedemption]) -> dict[str, Any]:
    status = CouponRedemption.Status
    return redemptions.aggregate(
        claimed=Count("id", filter=Q(status=status.CLAIMED)),
        applied=Count("id", filter=Q(status=status.APPLIED)),
        reversed=Count("id", filter=Q(status=status.REVERSED)),
        amount_applied=_money("amount", Q(status=status.APPLIED)),
        amount_reversed=_money("amount", Q(status=status.REVERSED)),
        students=Count("student", filter=Q(status=status.APPLIED), distinct=True),
    )


def redemption_totals_by_batch(
    redemptions: QuerySet[CouponRedemption],
) -> dict[int, dict[str, Any]]:
    status = CouponRedemption.Status
    rows = (
        redemptions.values("coupon__batch_id")
        .annotate(
            claimed=Count("id", filter=Q(status=status.CLAIMED)),
            applied=Count("id", filter=Q(status=status.APPLIED)),
            reversed=Count("id", filter=Q(status=status.REVERSED)),
            amount_applied=_money("amount", Q(status=status.APPLIED)),
        )
        .order_by()
    )
    return {row["coupon__batch_id"]: row for row in rows}
