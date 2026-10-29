"""
Every database read behind the collection report.

Functions here take filters and give back rows or aggregates. They shape
nothing - no percentages, no labels, no rounding. That belongs to
`services/collection_report.py`, which keeps the SQL in this module readable
and the arithmetic in that one testable.

A note on waived fees, because it decides half the numbers below: waiving
forgives whatever `balance` was left (see `fees/services/status_service.py`),
so a waived fee keeps its unpaid remainder in `balance` and looks exactly
like an unpaid fee to a naive `Sum`. Counting it as outstanding would inflate
what students owe, and counting it in a collection rate would drag the rate
down for money nobody ever expected to receive. So everything money-shaped
runs off `billable()` - the same `.exclude(status=WAIVED)` the admin
dashboard already uses - while the status breakdown, which is meant to
*count* waived fees, runs off the full set. The same rule reaches payments:
money received on a fee that was later waived is excluded from every
collection figure and reported in the losses section instead, so the
payment-derived totals and the fee-derived totals describe the same fees.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from django.db.models import Count, DecimalField, F, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth

from api.fees.models import Fee
from api.payment_submissions.models import (
    PaymentSubmission,
    PaymentSubmissionItems,
)
from api.payments.models import Payment
from api.programs.models import Program

from .periods import ReportFilters

ZERO = Value(
    Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2)
)

# Pass this as `institute_id` to mean "no institute at all", as opposed to
# None, which means "every institute". Primary keys start at 1, so it matches
# nothing. `services.common.institute_scope()` hands it to staff accounts
# that have no institute attached, so they get an empty report instead of
# everybody's figures.
NO_INSTITUTE = 0


def _money(expression) -> Coalesce:
    """`Sum` that returns 0.00 instead of None on an empty set."""
    return Coalesce(Sum(expression), ZERO)


# --------------------------------------------------------------------------
# Base querysets
# --------------------------------------------------------------------------


def fee_queryset(
    *, filters: ReportFilters, institute_id: Optional[int]
) -> QuerySet[Fee]:
    """
    Fees inside the reporting window.

    `institute_id` of None means no institute filter at all, which is only
    ever the case for a superuser with no institute of their own.
    """
    queryset = Fee.objects.all()

    if institute_id is not None:
        queryset = queryset.filter(student__program__institute_id=institute_id)

    if filters.uses_date_range:
        # A fee belongs to the window it was *issued* in.
        if filters.date_from:
            queryset = queryset.filter(created_at__date__gte=filters.date_from)
        if filters.date_to:
            queryset = queryset.filter(created_at__date__lte=filters.date_to)
    else:
        if filters.academic_year:
            queryset = queryset.filter(academic_year=filters.academic_year)
        if filters.semester:
            queryset = queryset.filter(semester=filters.semester)

    if filters.program_name:
        queryset = queryset.filter(student__program__name=filters.program_name)
    if filters.s_set:
        queryset = queryset.filter(student__s_set=filters.s_set)
    if filters.year_level is not None:
        queryset = queryset.filter(student__s_lvl=filters.year_level)

    return queryset


def payment_queryset(
    *, filters: ReportFilters, institute_id: Optional[int]
) -> QuerySet[Payment]:
    """
    Payments inside the reporting window - the same filters, crossed over the
    fee relation.

    Note the asymmetry with fees on a date range: a payment belongs to the
    window it was *received* in, not the window its fee was issued in. That is
    what makes `total_collections` mean "money that came in during these
    dates", which is the question the report is asking.

    Cancelled payments are excluded at the base: money that was reversed was
    never collected, whatever window it briefly passed through. Payments on
    fees that were later waived are excluded too - the fee-derived figures
    (category collected, outstanding, program sets) skip waived fees
    entirely, so keeping their payments here would let the two halves of the
    report disagree. That money is reported by the losses section instead.
    """
    queryset = Payment.objects.filter(
        status=Payment.PaymentStatus.ACTIVE
    ).exclude(fee__status=Fee.FeeStatus.WAIVED)

    if institute_id is not None:
        queryset = queryset.filter(
            fee__student__program__institute_id=institute_id
        )

    if filters.uses_date_range:
        if filters.date_from:
            queryset = queryset.filter(created_at__date__gte=filters.date_from)
        if filters.date_to:
            queryset = queryset.filter(created_at__date__lte=filters.date_to)
    else:
        if filters.academic_year:
            queryset = queryset.filter(fee__academic_year=filters.academic_year)
        if filters.semester:
            queryset = queryset.filter(fee__semester=filters.semester)

    if filters.program_name:
        queryset = queryset.filter(
            fee__student__program__name=filters.program_name
        )
    if filters.s_set:
        queryset = queryset.filter(fee__student__s_set=filters.s_set)
    if filters.year_level is not None:
        queryset = queryset.filter(fee__student__s_lvl=filters.year_level)

    return queryset


def billable(fees: QuerySet[Fee]) -> QuerySet[Fee]:
    """Fees that someone is actually expected to pay. See the module docstring."""
    return fees.exclude(status=Fee.FeeStatus.WAIVED)


# --------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------


def payment_totals(payments: QuerySet[Payment]) -> dict[str, Decimal]:
    """Collected overall, and split by the two payment methods."""
    return payments.aggregate(
        total=_money("amount_paid"),
        cash=_money_by_method(Payment.PaymentMethod.CASH),
        gcash=_money_by_method(Payment.PaymentMethod.GCASH),
    )


def _money_by_method(method: str) -> Coalesce:
    return Coalesce(
        Sum("amount_paid", filter=Q(payment_method=method)),
        ZERO,
    )


def outstanding_total(billable_fees: QuerySet[Fee]) -> Decimal:
    return billable_fees.aggregate(value=_money("balance"))["value"]


def status_counts(fees: QuerySet[Fee]) -> dict[str, int]:
    """
    Fee counts per status.

    There is no PARTIAL status in this system - a part-paid fee stays PENDING
    with a reduced balance - so `pending` here is exactly the pending rows.
    """
    return fees.aggregate(
        paid=Count("id", filter=Q(status=Fee.FeeStatus.PAID)),
        pending=Count("id", filter=Q(status=Fee.FeeStatus.PENDING)),
        overdue=Count("id", filter=Q(status=Fee.FeeStatus.OVERDUE)),
        waived=Count("id", filter=Q(status=Fee.FeeStatus.WAIVED)),
    )


def category_rows(fees: QuerySet[Fee]) -> list[dict[str, Any]]:
    """
    Per-category money, plus a count for every fee status.

    This one runs off the *full* fee set rather than `billable()`, because a
    waived fee still has to be counted somewhere. The money columns carry the
    waived filter themselves, so a waived fee shows up in `waived_count` and
    nowhere else - it neither inflates what was expected nor pretends to have
    been collected.
    """
    billable_only = ~Q(status=Fee.FeeStatus.WAIVED)

    return list(
        fees.values("category__category_name")
        .annotate(
            # Gross, before relief. `expected` is what is still owed after
            # discounts, which is the figure a collection target is set
            # against; both are reported because they answer different
            # questions and their difference is the relief granted.
            billed=Coalesce(Sum("total_amount", filter=billable_only), ZERO),
            discounts=Coalesce(Sum("discount_total", filter=billable_only), ZERO),
            expected=Coalesce(
                Sum(F("total_amount") - F("discount_total"), filter=billable_only),
                ZERO,
            ),
            # What has been paid off, derived from the fees themselves rather
            # than from Payment rows: a fee edited or written down outside the
            # payment flow still reconciles.
            collected=Coalesce(
                Sum(
                    F("total_amount") - F("discount_total") - F("balance"),
                    filter=billable_only,
                ),
                ZERO,
            ),
            outstanding=Coalesce(Sum("balance", filter=billable_only), ZERO),
            fee_count=Count("id"),
            paid_count=Count("id", filter=Q(status=Fee.FeeStatus.PAID)),
            pending_count=Count("id", filter=Q(status=Fee.FeeStatus.PENDING)),
            overdue_count=Count("id", filter=Q(status=Fee.FeeStatus.OVERDUE)),
            waived_count=Count("id", filter=Q(status=Fee.FeeStatus.WAIVED)),
        )
        .order_by("-collected")
    )


def staff_rows(payments: QuerySet[Payment]) -> list[dict[str, Any]]:
    """Who received the money, how many transactions, how much."""
    return list(
        payments.exclude(received_by__isnull=True)
        .values("received_by__username")
        .annotate(tx_count=Count("id"), collected=_money("amount_paid"))
        .order_by("-collected")
    )


def monthly_method_rows(payments: QuerySet[Payment]) -> list[dict[str, Any]]:
    """Cash and GCash totals per calendar month."""
    return list(
        payments.annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(
            cash=_money_by_method(Payment.PaymentMethod.CASH),
            gcash=_money_by_method(Payment.PaymentMethod.GCASH),
        )
        .order_by("month")
    )


def monthly_category_rows(payments: QuerySet[Payment]) -> list[dict[str, Any]]:
    """Collected per calendar month per category - the raw trend series."""
    return list(
        payments.annotate(month=TruncMonth("created_at"))
        .values("month", "fee__category__category_name")
        .annotate(collected=_money("amount_paid"))
        .order_by("month")
    )


def program_set_rows(billable_fees: QuerySet[Fee]) -> list[dict[str, Any]]:
    """
    Expected vs collected for each program / year level / set grouping.

    Comes back unordered - the service sorts, because "blank set last" is not
    something ORDER BY expresses the same way on every backend. The
    `.order_by()` is still load-bearing: without it, Fee's default ordering
    leaks `updated_at` into the GROUP BY and splits every group apart.
    """
    return list(
        billable_fees.values(
            "student__program__name", "student__s_lvl", "student__s_set"
        )
        .annotate(
            billed=_money("total_amount"),
            discounts=_money("discount_total"),
            expected=_money(F("total_amount") - F("discount_total")),
            collected=_money(F("total_amount") - F("discount_total") - F("balance")),
            outstanding=_money("balance"),
        )
        .order_by()
    )


def payment_submission_queryset(
    *, filters: ReportFilters, institute_id: Optional[int]
) -> QuerySet[PaymentSubmission]:
    """
    Student-submitted payment proofs inside the reporting window.

    A submission is dated by when the student sent it, not by when it was
    reviewed - the question the report asks is how much came in during the
    period, and a submission sitting unreviewed is exactly what the pending
    figure is for.
    """
    queryset = PaymentSubmission.objects.all()

    if institute_id is not None:
        queryset = queryset.filter(student__program__institute_id=institute_id)

    if filters.uses_date_range:
        if filters.date_from:
            queryset = queryset.filter(created_at__date__gte=filters.date_from)
        if filters.date_to:
            queryset = queryset.filter(created_at__date__lte=filters.date_to)
    else:
        # A submission has no term of its own; it inherits one from the fees
        # it is paying off.
        if filters.academic_year:
            queryset = queryset.filter(
                payment_submission_list__fee__academic_year=filters.academic_year
            )
        if filters.semester:
            queryset = queryset.filter(
                payment_submission_list__fee__semester=filters.semester
            )

    if filters.program_name:
        queryset = queryset.filter(student__program__name=filters.program_name)
    if filters.s_set:
        queryset = queryset.filter(student__s_set=filters.s_set)
    if filters.year_level is not None:
        queryset = queryset.filter(student__s_lvl=filters.year_level)

    # One submission can cover several fees, and the join above returns it
    # once per fee. Collapsing back to distinct rows *before* anything is
    # summed is what stops a two-fee submission being counted twice -
    # `.distinct()` would deduplicate the rows but not the Sum.
    return PaymentSubmission.objects.filter(pk__in=queryset.values("pk"))


def submission_stats(
    submissions: QuerySet[PaymentSubmission],
) -> dict[str, Any]:
    """Counts and amounts per submission status, in one pass."""
    status = PaymentSubmission.SubmissionStatus

    return submissions.aggregate(
        total_count=Count("id"),
        total_amount=_money("total_amount_paid"),
        approved_count=Count("id", filter=Q(status=status.APPROVED)),
        approved_amount=_submitted_by_status(status.APPROVED),
        pending_count=Count("id", filter=Q(status=status.PENDING)),
        pending_amount=_submitted_by_status(status.PENDING),
        rejected_count=Count("id", filter=Q(status=status.REJECTED)),
        rejected_amount=_submitted_by_status(status.REJECTED),
        # Approved-then-undone. Its money is already absent from every
        # payment aggregate (the payments were cancelled with it); this
        # bucket is what keeps the statuses summing back to `total`.
        cancelled_count=Count("id", filter=Q(status=status.CANCELLED)),
        cancelled_amount=_submitted_by_status(status.CANCELLED),
        # Recalled by the student before review - no money ever moved.
        withdrawn_count=Count("id", filter=Q(status=status.WITHDRAWN)),
        withdrawn_amount=_submitted_by_status(status.WITHDRAWN),
    )


def _submitted_by_status(status: str) -> Coalesce:
    return Coalesce(Sum("total_amount_paid", filter=Q(status=status)), ZERO)


def loss_fee_rows(fees: QuerySet[Fee]) -> list[dict[str, Any]]:
    """
    Every fee in the window with the columns the losses section reads:
    identity, money, and remarks (a waived fee's remarks carry the reason).

    One flat query serves both halves of the section - the waived list and
    the payment reconciliation - so they can never be cut differently.
    """
    return list(
        fees.values(
            "id",
            "status",
            "total_amount",
            "discount_total",
            "balance",
            "remarks",
            "category__category_name",
            "student__s_studentID",
            "student__s_fname",
            "student__s_mname",
            "student__s_lname",
            "student__s_suffix",
            "student__program__name",
            "student__s_lvl",
            "student__s_set",
        ).order_by("category__category_name", "pk")
    )


def payment_totals_by_fee(fees: QuerySet[Fee]) -> dict[int, Decimal]:
    """
    Sum of *active* payments per fee, for reconciling against the fee.

    The `.order_by()` clears Payment's default `-updated_at` ordering so it
    cannot leak into the GROUP BY and split a fee's payments apart - same
    trap as `program_set_rows`.
    """
    rows = (
        Payment.objects.filter(
            fee_id__in=fees.values("pk"),
            status=Payment.PaymentStatus.ACTIVE,
        )
        .values("fee_id")
        .annotate(total=_money("amount_paid"))
        .order_by()
    )
    return {row["fee_id"]: row["total"] for row in rows}


def points_discount_totals_by_fee(fees: QuerySet[Fee]) -> dict[int, Decimal]:
    """
    Relief funded by contribution points, per fee - approved discounts only.

    Read straight from the discount ledger rather than from
    `Fee.discount_total`, so the losses section can say *which kind* of
    relief a fee got. A fee whose `discount_total` exceeds the sum of the two
    ledgers was written down by hand, and the report shows that remainder on
    its own rather than hiding it inside one of them.

    The contributions app is always installed - only its routes are optional
    - so the table exists whatever the feature flags say; a deployment that
    never ran the scheme simply has no rows.
    """
    from api.contributions.models import FeeDiscount

    rows = (
        FeeDiscount.objects.filter(
            fee_id__in=fees.values("pk"),
            status=FeeDiscount.Status.APPROVED,
        )
        .values("fee_id")
        .annotate(total=_money("amount"))
        # Clears the model's default ordering - see payment_totals_by_fee.
        .order_by()
    )
    return {row["fee_id"]: row["total"] for row in rows}


def coupon_discount_totals_by_fee(fees: QuerySet[Fee]) -> dict[int, Decimal]:
    """Relief funded by coupons, per fee - applied redemptions only."""
    from api.coupons.models import CouponRedemption

    rows = (
        CouponRedemption.objects.filter(
            fee_id__in=fees.values("pk"),
            status=CouponRedemption.Status.APPLIED,
        )
        .values("fee_id")
        .annotate(total=_money("amount"))
        .order_by()
    )
    return {row["fee_id"]: row["total"] for row in rows}


def approved_item_totals_by_fee(fees: QuerySet[Fee]) -> dict[int, Decimal]:
    """
    Sum of approved submission items per fee.

    An approved item should have become a payment; comparing this figure with
    the payment total is how the reconciliation spots a submission that was
    approved but whose money never landed on the fee.
    """
    status = PaymentSubmission.SubmissionStatus
    rows = (
        PaymentSubmissionItems.objects.filter(
            fee_id__in=fees.values("pk"),
            payment_submission__status=status.APPROVED,
        )
        .values("fee_id")
        .annotate(total=_money("amount_paid"))
        # Clears the model's default ordering - see payment_totals_by_fee.
        .order_by()
    )
    return {row["fee_id"]: row["total"] for row in rows}


def program_names(institute_id: Optional[int]) -> list[str]:
    """
    Programs offered, for the filter dropdown.

    Deliberately not derived from the fees in the window: a program with no
    fees yet still has to be selectable, otherwise it can never be filtered
    to.
    """
    queryset = Program.objects.all()
    if institute_id is not None:
        queryset = queryset.filter(institute_id=institute_id)
    return list(queryset.order_by("name").values_list("name", flat=True).distinct())
