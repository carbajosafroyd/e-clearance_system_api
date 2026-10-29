"""
Read-side queries: the ranking and the dashboard/report figures.

Kept apart from `services.py` on purpose - nothing here writes, so it is safe
to call from anywhere, and the aggregate shapes stay in one place instead of
being re-derived in three views.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from django.db.models import Count, DecimalField, IntegerField, Q, Sum, Value
from django.db.models.functions import Coalesce

from core.choices import SystemScope

from .models import Contribution, FeeDiscount, PointAccount

ZERO_INT = Value(0, output_field=IntegerField())
ZERO_MONEY = Value(Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2))


def _scoped(queryset, *, institute_id: Optional[int], path: str):
    """Narrow to one institute, following `path` to the institute id."""
    if institute_id is None:
        return queryset
    return queryset.filter(**{path: institute_id})


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def leaderboard_queryset(
    *,
    institute_id: Optional[int] = None,
    system: Optional[str] = None,
    search: Optional[str] = None,
):
    """The ranked board as a queryset, so it can be counted and paged.

    Ranked on `total_earned` rather than `balance`: spending your points on a
    discount is the *point* of earning them, and ranking on the balance would
    quietly punish the students who actually used the scheme.

    With `system` omitted the wallets are summed across systems, which is the
    overall board; with it set, the board is that system's alone.

    A queryset rather than a list because the board is paged: the paginator
    needs the total to say how many places there are, and slicing a list
    would mean loading every wallet in the institute to hand back ten rows.
    """
    accounts = PointAccount.objects.select_related("student__program")
    accounts = _scoped(
        accounts, institute_id=institute_id, path="student__program__institute_id"
    )
    if system:
        accounts = accounts.filter(system=system)

    # Searching a ranking is "where am I", so it filters rather than jumping:
    # the row keeps the place it holds on the whole board, which is the only
    # number anybody wanted.
    term = (search or "").strip()
    if term:
        accounts = accounts.filter(
            Q(student__s_fname__icontains=term)
            | Q(student__s_lname__icontains=term)
            | Q(student__s_studentID__icontains=term)
            | Q(student__program__acronym__icontains=term)
        )

    return (
        accounts.values(
            "student_id",
            "student__s_studentID",
            "student__s_fname",
            "student__s_lname",
            "student__s_suffix",
            "student__program__acronym",
        )
        .annotate(
            points_earned=Coalesce(Sum("total_earned"), ZERO_INT),
            points_spent=Coalesce(Sum("total_spent"), ZERO_INT),
            points_balance=Coalesce(Sum("balance"), ZERO_INT),
        )
        .filter(points_earned__gt=0)
        .order_by("-points_earned", "student__s_lname")
    )


def leaderboard_ranks(
    *,
    institute_id: Optional[int] = None,
    system: Optional[str] = None,
) -> dict[int, int]:
    """Every ranked student's place on the *whole* board, by student id.

    Needed the moment the board is searched. A filtered page numbered by its
    own position would tell somebody looking themselves up that they came
    first, when the search is asking the opposite question - "where am I?".

    Competition ranking, matching `student_rank`: how many are strictly
    ahead, plus one. Two students on the same points share a place rather
    than one of them being arbitrarily demoted by their surname.
    """
    accounts = PointAccount.objects.all()
    accounts = _scoped(
        accounts, institute_id=institute_id, path="student__program__institute_id"
    )
    if system:
        accounts = accounts.filter(system=system)

    totals = list(
        accounts.values("student_id")
        .annotate(points_earned=Coalesce(Sum("total_earned"), ZERO_INT))
        .filter(points_earned__gt=0)
    )
    scores = sorted((row["points_earned"] for row in totals), reverse=True)

    # One pass over the sorted scores gives every distinct score its place;
    # counting per student would be quadratic on a board of any size.
    place_of_score: dict[int, int] = {}
    for index, score in enumerate(scores, start=1):
        place_of_score.setdefault(score, index)

    return {
        row["student_id"]: place_of_score[row["points_earned"]] for row in totals
    }


def leaderboard_rows(
    rows,
    *,
    system: Optional[str] = None,
    start_rank: int = 1,
    ranks: Optional[dict[int, int]] = None,
) -> list[dict[str, Any]]:
    """Turn a slice of the board into entries.

    `start_rank` is the board position of the first row, not 1: on page three
    the first entry is 21st, and a page that renumbered from 1 would tell four
    different students they had won.

    `ranks` overrides that counting entirely, for when the rows are a filtered
    subset and their position in the page says nothing about their place.

    The approved-contribution counts come from a second query rather than a
    join: summing the wallets across a join to contributions multiplies each
    wallet by the number of matching rows, so a student with three approved
    reports appeared to have earned three times what they did.
    """
    rows = list(rows)

    approved = Contribution.objects.filter(
        status=Contribution.Status.APPROVED,
        student_id__in=[row["student_id"] for row in rows],
    )
    if system:
        approved = approved.filter(system=system)
    counts = dict(
        approved.values_list("student_id").annotate(total=Count("id")).values_list(
            "student_id", "total"
        )
    )

    return [
        {
            "rank": (ranks or {}).get(row["student_id"], index),
            "student_id": row["student_id"],
            "student_school_id": row["student__s_studentID"],
            "student_name": " ".join(
                part
                for part in (
                    row["student__s_fname"],
                    row["student__s_lname"],
                    row["student__s_suffix"],
                )
                if part
            ),
            "program": row["student__program__acronym"],
            "system": system or "overall",
            "points_earned": row["points_earned"],
            "points_spent": row["points_spent"],
            "points_balance": row["points_balance"],
            "approved_contributions": counts.get(row["student_id"], 0),
        }
        for index, row in enumerate(rows, start=start_rank)
    ]


def leaderboard(
    *,
    institute_id: Optional[int] = None,
    system: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """The top `limit` places, already decorated. The unpaged read."""
    queryset = leaderboard_queryset(
        institute_id=institute_id, system=system, search=search
    )
    return leaderboard_rows(queryset[:limit], system=system)


def student_rank(
    *, student, institute_id: Optional[int] = None, system: Optional[str] = None
) -> Optional[int]:
    """Where one student sits on the same board, however far down.

    The leaderboard is capped; a student still wants to know they are 47th.
    """
    accounts = PointAccount.objects.all()
    accounts = _scoped(accounts, institute_id=institute_id, path="student__program__institute_id")
    if system:
        accounts = accounts.filter(system=system)

    totals = (
        accounts.values("student_id")
        .annotate(points_earned=Coalesce(Sum("total_earned"), ZERO_INT))
        .filter(points_earned__gt=0)
    )

    mine = next(
        (row["points_earned"] for row in totals if row["student_id"] == student.pk), 0
    )
    if not mine:
        return None

    ahead = sum(1 for row in totals if row["points_earned"] > mine)
    return ahead + 1


# ---------------------------------------------------------------------------
# Dashboard / report figures
# ---------------------------------------------------------------------------


def contribution_summary(
    *, institute_id: Optional[int] = None, system: Optional[str] = None
) -> dict[str, Any]:
    """Counts, points and money for the contributions dashboard."""
    contributions = _scoped(
        Contribution.objects.all(),
        institute_id=institute_id,
        path="student__program__institute_id",
    )
    discounts = _scoped(
        FeeDiscount.objects.all(),
        institute_id=institute_id,
        path="student__program__institute_id",
    )
    accounts = _scoped(
        PointAccount.objects.all(),
        institute_id=institute_id,
        path="student__program__institute_id",
    )

    if system:
        contributions = contributions.filter(system=system)
        discounts = discounts.filter(system=system)
        accounts = accounts.filter(system=system)

    status_counts = contributions.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=Contribution.Status.PENDING)),
        under_review=Count("id", filter=Q(status=Contribution.Status.UNDER_REVIEW)),
        approved=Count("id", filter=Q(status=Contribution.Status.APPROVED)),
        rejected=Count("id", filter=Q(status=Contribution.Status.REJECTED)),
        duplicate=Count("id", filter=Q(status=Contribution.Status.DUPLICATE)),
    )

    points = accounts.aggregate(
        earned=Coalesce(Sum("total_earned"), ZERO_INT),
        spent=Coalesce(Sum("total_spent"), ZERO_INT),
        outstanding=Coalesce(Sum("balance"), ZERO_INT),
    )

    money = discounts.aggregate(
        granted=Coalesce(
            Sum("amount", filter=Q(status=FeeDiscount.Status.APPROVED)), ZERO_MONEY
        ),
        pending=Coalesce(
            Sum("amount", filter=Q(status=FeeDiscount.Status.PENDING)), ZERO_MONEY
        ),
        cancelled=Coalesce(
            Sum("amount", filter=Q(status=FeeDiscount.Status.CANCELLED)), ZERO_MONEY
        ),
    )

    return {
        "system": system or "overall",
        "contributions": status_counts,
        "points": points,
        "discounts": {
            "count": discounts.filter(status=FeeDiscount.Status.APPROVED).count(),
            "amount_granted": money["granted"],
            "amount_pending": money["pending"],
            "amount_cancelled": money["cancelled"],
            "students_helped": discounts.filter(status=FeeDiscount.Status.APPROVED)
            .values("student_id")
            .distinct()
            .count(),
        },
        "by_system": system_breakdown(institute_id=institute_id),
        "by_kind": kind_breakdown(institute_id=institute_id, system=system),
    }


def system_breakdown(*, institute_id: Optional[int] = None) -> list[dict[str, Any]]:
    """One row per system, zero-filled - so a quiet system still shows up."""
    contributions = _scoped(
        Contribution.objects.all(),
        institute_id=institute_id,
        path="student__program__institute_id",
    )
    discounts = _scoped(
        FeeDiscount.objects.filter(status=FeeDiscount.Status.APPROVED),
        institute_id=institute_id,
        path="student__program__institute_id",
    )

    contribution_rows = {
        row["system"]: row
        for row in contributions.values("system").annotate(
            total=Count("id"),
            approved=Count("id", filter=Q(status=Contribution.Status.APPROVED)),
            pending=Count("id", filter=Q(status=Contribution.Status.PENDING)),
            points=Coalesce(
                Sum("points_awarded", filter=Q(status=Contribution.Status.APPROVED)),
                ZERO_INT,
            ),
        )
    }
    discount_rows = {
        row["system"]: row
        for row in discounts.values("system").annotate(
            amount=Coalesce(Sum("amount"), ZERO_MONEY),
            count=Count("id"),
        )
    }

    return [
        {
            "system": value,
            "label": label,
            "total": contribution_rows.get(value, {}).get("total", 0),
            "approved": contribution_rows.get(value, {}).get("approved", 0),
            "pending": contribution_rows.get(value, {}).get("pending", 0),
            "points_awarded": contribution_rows.get(value, {}).get("points", 0),
            "discount_count": discount_rows.get(value, {}).get("count", 0),
            "discount_amount": discount_rows.get(value, {}).get(
                "amount", Decimal("0.00")
            ),
        }
        for value, label in SystemScope.choices
    ]


def kind_breakdown(
    *, institute_id: Optional[int] = None, system: Optional[str] = None
) -> list[dict[str, Any]]:
    contributions = _scoped(
        Contribution.objects.all(),
        institute_id=institute_id,
        path="student__program__institute_id",
    )
    if system:
        contributions = contributions.filter(system=system)

    return list(
        contributions.values("kind")
        .annotate(
            total=Count("id"),
            approved=Count("id", filter=Q(status=Contribution.Status.APPROVED)),
            points=Coalesce(
                Sum("points_awarded", filter=Q(status=Contribution.Status.APPROVED)),
                ZERO_INT,
            ),
        )
        .order_by("-total")
    )


def student_wallet(*, student) -> dict[str, Any]:
    """Every wallet a student holds, plus the totals across them."""
    accounts = list(PointAccount.objects.filter(student=student))
    by_system = {account.system: account for account in accounts}

    wallets = []
    for value, label in SystemScope.choices:
        account = by_system.get(value)
        wallets.append(
            {
                "system": value,
                "label": label,
                "balance": account.balance if account else 0,
                "total_earned": account.total_earned if account else 0,
                "total_spent": account.total_spent if account else 0,
            }
        )

    return {
        "wallets": wallets,
        "total_balance": sum(account.balance for account in accounts),
        "total_earned": sum(account.total_earned for account in accounts),
        "total_spent": sum(account.total_spent for account in accounts),
        "approved_contributions": Contribution.objects.filter(
            student=student, status=Contribution.Status.APPROVED
        ).count(),
        "pending_contributions": Contribution.objects.filter(
            student=student,
            status__in=[
                Contribution.Status.PENDING,
                Contribution.Status.UNDER_REVIEW,
            ],
        ).count(),
        "discount_total": FeeDiscount.objects.filter(
            student=student, status=FeeDiscount.Status.APPROVED
        ).aggregate(value=Coalesce(Sum("amount"), ZERO_MONEY))["value"],
    }


def fee_discount_history(*, fee):
    """Every discount ever requested against one fee, newest first.

    Rejected and cancelled rows are included on purpose: "we asked and were
    refused" is part of the history a student and an auditor both want.
    """
    return (
        FeeDiscount.objects.filter(fee=fee)
        .select_related("requested_by", "reviewed_by", "cancelled_by")
        .order_by("-created_at")
    )
