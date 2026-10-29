"""
Assembly of the collection report.

One function builds one snapshot, and both the JSON endpoint and the PDF
endpoint call it. That is the whole design: the figure printed on paper and
the figure on screen are produced by the same code path, so they can never
disagree.

Everything below is plain arithmetic over what `selectors.py` returned. No
queries live here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Optional

from django.utils import timezone

from api.fees.models import Fee

from .. import selectors
from ..constants import MAX_TREND_MONTHS
from ..periods import (
    ReportFilters,
    month_count,
    month_end,
    month_label,
    month_start,
    months_between,
    previous_period,
)
from .common import full_name, institute_scope, money, rate, submission_section


def build_collection_report(
    *, user, filters: ReportFilters
) -> dict[str, Any]:
    """
    The full snapshot, shaped exactly as the reports screen expects it.

    `user` decides the institute scope - see `common.institute_scope()`.
    """
    institute_id = institute_scope(user)

    fees = selectors.fee_queryset(filters=filters, institute_id=institute_id)
    billable_fees = selectors.billable(fees)
    payments = selectors.payment_queryset(
        filters=filters, institute_id=institute_id
    )

    totals = selectors.payment_totals(payments)
    outstanding = selectors.outstanding_total(billable_fees)
    categories = _categories(selectors.category_rows(fees))
    staff = _staff(selectors.staff_rows(payments))
    losses = _losses(fees)
    submissions = selectors.payment_submission_queryset(
        filters=filters, institute_id=institute_id
    )

    months, payment_methods, trend_series = _monthly(
        method_rows=selectors.monthly_method_rows(payments),
        category_rows=selectors.monthly_category_rows(payments),
        filters=filters,
        today=timezone.localdate(),
    )

    # "Top" is the best-performing category by collection rate, not the
    # biggest earner - a small category that is fully collected beats a large
    # one that is half paid.
    top = max(categories, key=lambda row: row["collection_rate"], default=None)

    return {
        "generated_at": timezone.now().isoformat(),
        "kpi": {
            "total_collections": money(totals["total"]),
            "previous_total_collections": _previous_total(
                user=user, filters=filters, institute_id=institute_id
            ),
            "total_outstanding": money(outstanding),
            "cash": money(totals["cash"]),
            "gcash": money(totals["gcash"]),
            "top_category": top["category_name"] if top else None,
            "top_category_rate": top["collection_rate"] if top else 0.0,
        },
        "status_breakdown": selectors.status_counts(fees),
        "categories": categories,
        # Money that will never arrive (waived balances) and money the ledger
        # cannot account for (fees whose paid-off amount disagrees with their
        # payments). See `_losses()`.
        "losses": losses,
        "payment_submissions": submission_section(
            selectors.submission_stats(submissions)
        ),
        "staff": staff,
        # One series per real collection category - nothing is bucketed into
        # hardcoded tuition/misc/activities keys any more. `labels` and
        # `series[].points` line up index for index; `months` carries the
        # exact dates behind each label.
        "trend": {
            "labels": [month["label"] for month in months],
            "months": months,
            "series": trend_series,
        },
        "payment_methods": payment_methods,
        "program_sets": _program_sets(selectors.program_set_rows(billable_fees)),
        "filter_options": {
            "programs": selectors.program_names(institute_id),
            "staff": [row["staff_username"] for row in staff],
        },
    }


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


def _categories(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "category_name": row["category__category_name"],
            # Rate against what was asked for after relief, not against the
            # gross bill: a category that collected everything it charged,
            # having discounted part of it, collected 100% of it.
            "collection_rate": rate(row["collected"], row["expected"]),
            "billed": money(row["billed"]),
            "discounts": money(row["discounts"]),
            "expected": money(row["expected"]),
            "collected": money(row["collected"]),
            "outstanding": money(row["outstanding"]),
            "fee_count": row["fee_count"],
            "paid_count": row["paid_count"],
            "pending_count": row["pending_count"],
            "overdue_count": row["overdue_count"],
            "waived_count": row["waived_count"],
        }
        for row in rows
    ]


def _losses(fees) -> dict[str, Any]:
    """
    The money that will not be collected, and the money that does not add up.

    Two halves, cut from the same fee rows:

    `waived` - every waived fee in the window. The loss is the *forgiven
    remainder* (`balance` at waive time), not the whole bill: anything paid
    before the waive was still collected and is reported alongside as
    `collected_before_waive`, because that money is deliberately excluded
    from every collection figure (see `selectors.payment_queryset`) and would
    otherwise vanish from the report entirely.

    `discrepancies` - the reconciliation the report runs over *every* fee:
    what the fee says was paid off (`total_amount - balance`) against the sum
    of its active payments. The two are written together by the payment
    services, so a mismatch means money moved outside the payment flow - a
    hand-edited balance, a deleted row. Approved submission items are summed
    alongside for context: an approved submission whose money never became a
    payment shows up here as a fee that reconciles on payments but not on
    submissions.

    `discounts` - every fee in the window that carries relief: points
    discounts, coupons, and anything written onto `discount_total` by other
    means. Relief is money the institute chose not to collect, which makes
    it the same kind of figure as a waiver - revenue given away on purpose,
    with a reason behind it - so it is accounted for here rather than left
    to be inferred from the gap between billed and expected. Each record
    splits the amount by what funded it, so a coordinator can answer "how
    much did the points scheme cost us this semester" from this section
    alone.

    `total_losses` = the waived amounts + the discounts + the reconciliation
    `shortfall`. The shortfall counts only *positive* differences - fees
    drawn down with no payment *and no recorded relief* behind the drawdown,
    i.e. revenue given away unrecorded. A negative difference (more payments
    than drawdown) is flagged as a discrepancy but is not missing money, so
    it neither reduces nor joins the loss total. A discount never reaches
    the shortfall: it is recorded relief, and it has its own line.
    """
    payment_totals = selectors.payment_totals_by_fee(fees)
    item_totals = selectors.approved_item_totals_by_fee(fees)
    points_totals = selectors.points_discount_totals_by_fee(fees)
    coupon_totals = selectors.coupon_discount_totals_by_fee(fees)

    waived_records: list[dict[str, Any]] = []
    waived_billed = waived_collected = waived_amount = 0.0
    waived_discounted = 0.0
    discount_records: list[dict[str, Any]] = []
    discount_amount = discount_points = discount_coupons = 0.0
    discount_other = 0.0
    discrepancy_records: list[dict[str, Any]] = []
    discrepancy_difference = discrepancy_shortfall = 0.0

    for row in selectors.loss_fee_rows(fees):
        name = full_name(
            row["student__s_fname"],
            row["student__s_mname"],
            row["student__s_lname"],
            row["student__s_suffix"],
        )
        # Net of relief. `total_amount - balance` would count every discount
        # as a drawdown with no payment behind it, and the check below reads
        # exactly that as revenue given away unrecorded - so without the
        # subtraction each legitimate discount books itself as a loss.
        fee_discount = money(row["discount_total"])
        fee_paid = money(row["total_amount"] - row["discount_total"] - row["balance"])
        payments_total = money(payment_totals.get(row["id"]))

        if row["status"] == Fee.FeeStatus.WAIVED:
            waived_billed += money(row["total_amount"])
            waived_collected += fee_paid
            waived_discounted += fee_discount
            waived_amount += money(row["balance"])
            waived_records.append(
                {
                    "fee_id": row["id"],
                    "category": row["category__category_name"],
                    "student_number": row["student__s_studentID"],
                    "student_name": name,
                    "program": row["student__program__name"],
                    "year_level": row["student__s_lvl"],
                    "set": row["student__s_set"] or "—",
                    "total_amount": money(row["total_amount"]),
                    "paid_before_waive": fee_paid,
                    # Relief granted on a fee that was then written off
                    # anyway - worth seeing, and not the same as money paid.
                    "discounted_before_waive": fee_discount,
                    "waived_amount": money(row["balance"]),
                    # The waive flow requires remarks, so this is the reason.
                    "remarks": row["remarks"] or None,
                }
            )

        if fee_discount > 0:
            points = money(points_totals.get(row["id"]))
            coupons = money(coupon_totals.get(row["id"]))
            # Whatever the two ledgers do not explain was written onto the
            # fee some other way - a hand edit, a migration backfill. Shown
            # rather than folded into either ledger, for the same reason the
            # reconciliation exists: unexplained money should look unexplained.
            other = money(fee_discount - points - coupons)
            discount_amount += fee_discount
            discount_points += points
            discount_coupons += coupons
            discount_other += other
            discount_records.append(
                {
                    "fee_id": row["id"],
                    "category": row["category__category_name"],
                    "student_number": row["student__s_studentID"],
                    "student_name": name,
                    "program": row["student__program__name"],
                    "year_level": row["student__s_lvl"],
                    "set": row["student__s_set"] or "—",
                    "status": row["status"],
                    "total_amount": money(row["total_amount"]),
                    "discount_total": fee_discount,
                    "points": points,
                    "coupons": coupons,
                    "other": other,
                    "balance": money(row["balance"]),
                }
            )

        difference = money(fee_paid - payments_total)
        if difference:
            discrepancy_difference += difference
            if difference > 0:
                discrepancy_shortfall += difference
            discrepancy_records.append(
                {
                    "fee_id": row["id"],
                    "category": row["category__category_name"],
                    "student_number": row["student__s_studentID"],
                    "student_name": name,
                    "status": row["status"],
                    "total_amount": money(row["total_amount"]),
                    "discount_total": fee_discount,
                    "balance": money(row["balance"]),
                    # What the fee says came in vs what the payments say.
                    "fee_paid": fee_paid,
                    "payments_total": payments_total,
                    "approved_submission_total": money(
                        item_totals.get(row["id"])
                    ),
                    "difference": difference,
                }
            )

    return {
        "waived": {
            "count": len(waived_records),
            "billed": money(waived_billed),
            "collected_before_waive": money(waived_collected),
            "discounted_before_waive": money(waived_discounted),
            "waived_amount": money(waived_amount),
            "records": waived_records,
        },
        # Relief granted on purpose. Waived fees included: a discount on a
        # fee that was later written off is still relief that was given,
        # and the waived line only ever counts the remainder after it.
        "discounts": {
            "count": len(discount_records),
            "amount": money(discount_amount),
            "points": money(discount_points),
            "coupons": money(discount_coupons),
            "other": money(discount_other),
            "records": discount_records,
        },
        "discrepancies": {
            "count": len(discrepancy_records),
            "difference": money(discrepancy_difference),
            # Positive differences only - the part of the mismatch that is
            # actually missing money, and the part that joins total_losses.
            "shortfall": money(discrepancy_shortfall),
            "records": discrepancy_records,
        },
        "total_losses": money(
            waived_amount + discount_amount + discrepancy_shortfall
        ),
    }


def _staff(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "staff_username": row["received_by__username"],
            "tx_count": row["tx_count"],
            "collected": money(row["collected"]),
        }
        for row in rows
    ]


def _program_sets(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    One row per program / year level / set, ordered for reading: program
    A-Z, then year level 1..n, then set A-Z with blank sets last.
    """

    def sort_key(row):
        set_value = row["student__s_set"]
        return (
            row["student__program__name"] or "",
            row["student__s_lvl"] or 0,
            not set_value,  # blank sets after named ones
            set_value or "",
        )

    return [
        {
            "program": row["student__program__name"] or "—",
            "year_level": row["student__s_lvl"],
            "set": row["student__s_set"] or "—",
            "billed": money(row["expected"]),
            "total": money(row["collected"]),
            "balance": money(row["outstanding"]),
            "rate": rate(row["collected"], row["expected"]),
        }
        for row in sorted(rows, key=sort_key)
    ]


def _monthly(
    *,
    method_rows: list[dict[str, Any]],
    category_rows: list[dict[str, Any]],
    filters: ReportFilters,
    today: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Both monthly series, built against one shared axis.

    The two queries can in principle disagree about which months exist, so the
    axis is decided once, up front, and both series are laid out on it. That
    guarantees "2026-02" means the same calendar month in the trend chart as
    it does in the payment-method chart.
    """
    observed = sorted(
        {_month_date(row["month"]) for row in method_rows if row["month"]}
        | {_month_date(row["month"]) for row in category_rows if row["month"]}
    )
    months = _month_axis(filters=filters, observed=observed, today=today)
    position = {month: index for index, month in enumerate(months)}

    # `total` is carried per row rather than derived by consumers: the PDF
    # template's `add` filter truncates floats to ints, and the Excel builder
    # would otherwise round its own way.
    payment_methods = [
        {**_month_meta(month), "cash": 0.0, "gcash": 0.0, "total": 0.0}
        for month in months
    ]
    for row in method_rows:
        index = position.get(_month_date(row["month"]))
        if index is None:
            continue
        payment_methods[index]["cash"] = money(row["cash"])
        payment_methods[index]["gcash"] = money(row["gcash"])
        payment_methods[index]["total"] = money(row["cash"] + row["gcash"])

    # One series per real collection category, every one aligned to the axis.
    series_points: dict[str, list[float]] = {}
    for row in category_rows:
        index = position.get(_month_date(row["month"]))
        if index is None:
            continue

        name = row["fee__category__category_name"] or "Uncategorised"
        points = series_points.setdefault(name, [0.0] * len(months))
        points[index] += money(row["collected"])

    trend_series = [
        {"name": name, "points": [round(point, 2) for point in points]}
        for name, points in sorted(series_points.items())
    ]

    month_meta = [_month_meta(month, key="label") for month in months]
    return month_meta, payment_methods, trend_series


def _month_axis(
    *, filters: ReportFilters, observed: list[date], today: date
) -> list[date]:
    """
    Which months the two series are drawn across.

    Anchored to the filter whenever there is a date range to anchor to, so a
    quiet month *inside* the selected period shows up as a zero instead of
    vanishing off the axis and making the chart look continuous when it is
    not. With an academic year or semester selected there are no explicit
    dates to work from, so the window runs from the first payment to the
    last - with every gap between them filled in.

    Months are only ever invented around real data: a period with no payments
    at all gets an empty axis rather than a row of zeroes.
    """
    if not observed:
        return []

    start, end = observed[0], observed[-1]

    if filters.uses_date_range:
        if filters.date_from:
            start = min(start, month_start(filters.date_from))
        # A date_to in the future would only pad the axis with months that can
        # never fill, since a payment cannot be recorded ahead of time.
        end = max(end, month_start(min(filters.date_to or today, today)))

    if month_count(start, end) > MAX_TREND_MONTHS:
        # Too long a span to draw month by month - fall back to the months
        # that actually saw money. Each label names its month, so the gaps
        # are obvious rather than hidden.
        return observed

    return months_between(start, end)


def _month_meta(month: date, *, key: str = "period") -> dict[str, Any]:
    """
    The identity of one month: a readable label, plus the exact dates behind
    it so the frontend can format them its own way.

    `key` exists only because the two series name this field differently -
    `period` on payment methods, `label` on the trend - and renaming either
    one would break the chart.
    """
    return {
        key: month_label(month),
        "start": month.isoformat(),
        "end": month_end(month).isoformat(),
    }


def _month_date(value) -> Optional[date]:
    """TruncMonth hands back a datetime; the axis works in plain dates."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.date()
    return value


def _previous_total(
    *, user, filters: ReportFilters, institute_id: Optional[int]
) -> Optional[float]:
    """
    Total collections over the window immediately before this one.

    None - not 0 - when there is no comparable window, so the frontend can say
    "no comparable previous period" instead of showing a fake -100%.
    """
    if not filters.compare_previous:
        return None

    earlier = previous_period(filters, today=timezone.localdate())
    if earlier is None:
        return None

    payments = selectors.payment_queryset(
        filters=earlier, institute_id=institute_id
    )
    return money(selectors.payment_totals(payments)["total"])
