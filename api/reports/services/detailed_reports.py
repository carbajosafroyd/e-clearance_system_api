"""
The three detailed reports: every fee, every payment, every student - not
aggregates but the rows behind them, with totals at every level of grouping.

All three are built the same way: one fee or payment queryset, fetched once
with its relations, then grouped and totalled in Python. The volume is
bounded by the parameter serializer, which refuses a request with no period -
"all history for the whole institute" is not a report, it is a database dump.

The totals rule is the same everywhere (see `common.FeeTotals`): billed,
paid and balance exclude waived fees; status fields still count them. Child
totals always sum exactly to their parent's, because parent and child are
fed the same fees through the same accumulator.

The detailed fees report additionally carries three flat sections -
`payments`, `payment_submissions` and `payment_submission_items` - each a
full-detail record list with its own totals underneath. Those are records
totals: they sum exactly the rows listed above them, which is what the
export tabs print under their tables. A fourth section, `losses`, lists the
waived fees with the payment history behind each one - see
`_losses_section`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from django.utils import timezone

from api.fees.models import Fee
from api.payment_submissions.models import (
    PaymentSubmission,
    PaymentSubmissionItems,
)
from api.payments.models import Payment
from api.utils.cloudinary.cloudinary_utils import get_file_url

from .. import selectors
from ..periods import ReportFilters
from .common import (
    FeeTotals,
    institute_scope,
    money,
    rate,
    student_name,
    submission_section,
)

# What a blank student set is displayed as, everywhere.
NO_SET = "—"

# The order status groups appear in inside each category of the detailed
# fees report: money still owed first, then settled, then never-owed.
STATUS_ORDER = (
    Fee.FeeStatus.OVERDUE,
    Fee.FeeStatus.PENDING,
    Fee.FeeStatus.PAID,
    Fee.FeeStatus.WAIVED,
)


# ==========================================================================
# Detailed fees
# ==========================================================================


def build_detailed_fees_report(*, user, filters: ReportFilters) -> dict[str, Any]:
    """
    Every fee in the window, grouped by category and, inside each category,
    by status - money still owed first (see STATUS_ORDER). Each fee carries
    the submissions and payments behind it.

    The no-repeat rule: `Payment.payment_submission` is a nullable FK, and it
    decides where a payment appears. A payment born from an approved
    submission sits under `fee.submissions[].payments`; an over-the-counter
    payment (no submission behind it) sits under `fee.payments`. Every
    payment appears exactly once.

    Alongside the nested `categories` tree the snapshot carries three flat
    sections - `payments`, `payment_submissions` and
    `payment_submission_items` - each every-column record lists with their
    own totals underneath, mirroring the export tabs: summary, fees,
    payments, payment submissions, payment submission items.
    """
    institute_id = institute_scope(user)

    fees = list(
        selectors.fee_queryset(filters=filters, institute_id=institute_id)
        .select_related("student__program", "category", "issued_by")
        .order_by(
            "category__category_name",
            "student__s_lname",
            "student__s_fname",
            "pk",
        )
    )
    fee_ids = [fee.pk for fee in fees]
    fees_by_id = {fee.pk: fee for fee in fees}

    # Every payment, cancelled ones included: the flat payments section lists
    # them side by side with an Active/Cancelled split so reversed money is
    # visible instead of silently absent. The *nested* tree and the summary
    # stay active-only - a cancelled payment's money was put back on the fee,
    # so the fee-derived numbers (total_amount - balance) already reflect the
    # reversal and only active rows sum back to them.
    all_payments = list(
        Payment.objects.filter(fee_id__in=fee_ids)
        .select_related("received_by", "payment_submission", "cancelled_by")
        .order_by("created_at", "pk")
    )
    payments = [
        payment
        for payment in all_payments
        if payment.status == Payment.PaymentStatus.ACTIVE
    ]

    # A submission belongs to the report if it touches a listed fee - through
    # an item (the student's claim) or through a payment (the money that
    # actually landed). Either link alone is enough: a submission approved
    # into payments but missing its item row would otherwise vanish.
    submission_ids = set(
        PaymentSubmissionItems.objects.filter(fee_id__in=fee_ids).values_list(
            "payment_submission_id", flat=True
        )
    )
    submission_ids.update(
        payment.payment_submission_id
        for payment in all_payments
        if payment.payment_submission_id is not None
    )

    submissions = list(
        PaymentSubmission.objects.filter(pk__in=submission_ids)
        .select_related("student", "reviewed_by")
        .order_by("created_at", "pk")
    )

    # Every item of every listed submission - even one whose fee sits outside
    # the window - so the items section always sums back to its submissions.
    items = list(
        PaymentSubmissionItems.objects.filter(
            payment_submission_id__in=submission_ids
        )
        .select_related(
            "payment_submission__reviewed_by",
            "fee__student",
            "fee__category",
        )
        .order_by(
            "payment_submission__created_at", "payment_submission_id", "pk"
        )
    )

    # Sort every payment into exactly one bucket - that IS the no-repeat rule.
    direct: dict[int, list[Payment]] = defaultdict(list)
    linked: dict[tuple[int, int], list[Payment]] = defaultdict(list)
    for payment in payments:
        if payment.payment_submission_id is None:
            direct[payment.fee_id].append(payment)
        else:
            linked[(payment.fee_id, payment.payment_submission_id)].append(payment)

    fee_items: dict[int, list[PaymentSubmissionItems]] = defaultdict(list)
    for item in items:
        fee_items[item.fee_id].append(item)

    # Group by category, then by status inside each category. The fees
    # arrive sorted by category name and student surname, so insertion order
    # is already the final order everywhere below.
    by_category: dict[str, list[Fee]] = defaultdict(list)
    for fee in fees:
        by_category[fee.category.category_name].append(fee)

    categories: list[dict[str, Any]] = []
    grand = FeeTotals()

    for name, category_fees in by_category.items():
        category_totals = FeeTotals()
        groups: dict[str, dict[str, Any]] = {}

        for fee in category_fees:
            category_totals.add(fee)
            grand.add(fee)
            group = groups.setdefault(
                fee.status, {"totals": FeeTotals(), "fees": []}
            )
            group["totals"].add(fee)
            group["fees"].append(_detailed_fee(fee, fee_items, linked, direct))

        # STATUS_ORDER first; anything unforeseen tags along at the end
        # rather than silently vanishing.
        ordered = [s for s in STATUS_ORDER if s in groups]
        ordered += [s for s in groups if s not in STATUS_ORDER]

        categories.append(
            {
                "category_name": name,
                "totals": category_totals.shape(),
                "statuses": [
                    {
                        "status": str(status),
                        "count": len(groups[status]["fees"]),
                        # Same rule as everywhere: the waived group counts
                        # its fees but contributes zero money, so status
                        # groups still sum exactly to the category total.
                        "totals": groups[status]["totals"].shape(),
                        "fees": groups[status]["fees"],
                    }
                    for status in ordered
                ],
            }
        )

    return {
        "generated_at": timezone.now().isoformat(),
        "summary": _detailed_summary(fees, payments, submissions),
        "categories": categories,
        "totals": grand.shape(),
        "payments": _payments_section(all_payments, fees_by_id),
        "payment_submissions": _submissions_section(submissions, items),
        "payment_submission_items": _items_section(items),
        "losses": _losses_section(fees, all_payments, items),
    }


def _screenshot_urls(submission: PaymentSubmission) -> list[str]:
    """
    The Cloudinary links behind a submission's proof-of-payment screenshots.

    `screenshot` stores Cloudinary public ids, not URLs - the link is derived
    here the same way the payment-submission endpoints derive it. If the
    Cloudinary config is absent (a bare dev environment), the public id
    itself is returned rather than crashing the whole report over a link.
    """
    urls = []
    for public_id in submission.screenshot or []:
        try:
            urls.append(get_file_url(public_id, version=submission.cloudinary_version))
        except Exception:
            urls.append(public_id)
    return urls


def _payments_section(
    payments: list[Payment], fees_by_id: dict[int, Fee]
) -> dict[str, Any]:
    """
    Every payment behind the listed fees - cancelled ones included - flat,
    with totals underneath.

    The totals come in three layers: `active` (the money actually held),
    `cancelled` (money that was recorded and reversed), and the top-level
    grand count/amount that sums every listed row. Only the active layer
    carries the cash/GCash split - a cancelled payment's method is history,
    not a drawer to balance.
    """
    records = []
    active_total = active_cash = active_gcash = cancelled_total = 0.0
    active_count = cancelled_count = 0

    for payment in payments:
        fee = fees_by_id[payment.fee_id]
        amount = float(payment.amount_paid)
        if payment.status == Payment.PaymentStatus.ACTIVE:
            active_count += 1
            active_total += amount
            if payment.payment_method == Payment.PaymentMethod.CASH:
                active_cash += amount
            elif payment.payment_method == Payment.PaymentMethod.GCASH:
                active_gcash += amount
        else:
            cancelled_count += 1
            cancelled_total += amount
        records.append(
            {
                "id": payment.pk,
                "fee_id": payment.fee_id,
                "category": fee.category.category_name,
                "student_number": fee.student.s_studentID,
                "student_name": student_name(fee.student),
                "previous_balance": money(payment.previous_balance),
                "amount_paid": money(payment.amount_paid),
                "payment_method": payment.payment_method,
                # Where the money came from: "submission" when it was born
                # from an approved student submission, "direct" for over
                # the counter.
                "source": (
                    "submission" if payment.payment_submission_id else "direct"
                ),
                "payment_submission_id": payment.payment_submission_id,
                "submission_reference": (
                    payment.payment_submission.reference_number
                    if payment.payment_submission_id
                    else None
                ),
                "received_by": (
                    payment.received_by.username if payment.received_by else None
                ),
                "status": payment.status,
                "cancelled_by": (
                    payment.cancelled_by.username
                    if payment.cancelled_by
                    else None
                ),
                "cancelled_at": (
                    payment.cancelled_at.isoformat()
                    if payment.cancelled_at
                    else None
                ),
                "cancellation_reason": payment.cancellation_reason or None,
                "created_at": payment.created_at.isoformat(),
                "updated_at": payment.updated_at.isoformat(),
            }
        )

    return {
        "records": records,
        "totals": {
            "count": len(records),
            "amount_paid": money(active_total + cancelled_total),
            "active": {
                "count": active_count,
                "amount_paid": money(active_total),
                "cash": money(active_cash),
                "gcash": money(active_gcash),
            },
            "cancelled": {
                "count": cancelled_count,
                "amount_paid": money(cancelled_total),
            },
        },
    }


def _losses_section(
    fees: list[Fee],
    all_payments: list[Payment],
    items: list[PaymentSubmissionItems],
) -> dict[str, Any]:
    """
    The waived fees, each with the payment history behind it.

    A waived fee's loss is the forgiven remainder (`balance`), not the whole
    bill - anything paid before the waive was real money and is listed right
    under the fee: its payments (cancelled ones flagged) and its submission
    items, so the reader can see exactly what came in before the rest was
    written off.
    """
    payments_by_fee: dict[int, list[Payment]] = defaultdict(list)
    for payment in all_payments:
        payments_by_fee[payment.fee_id].append(payment)
    items_by_fee: dict[int, list[PaymentSubmissionItems]] = defaultdict(list)
    for item in items:
        items_by_fee[item.fee_id].append(item)

    records = []
    billed = collected = waived_amount = discounted = 0.0

    for fee in fees:
        if fee.status != Fee.FeeStatus.WAIVED:
            continue
        billed += float(fee.total_amount)
        # Net of relief: without it, a discount granted before the waive
        # would be reported as money the student had paid.
        collected += float(fee.total_amount - fee.discount_total - fee.balance)
        discounted += float(fee.discount_total)
        waived_amount += float(fee.balance)
        records.append(
            {
                "fee_id": fee.pk,
                "category": fee.category.category_name,
                "student_number": fee.student.s_studentID,
                "student_name": student_name(fee.student),
                "program": fee.student.program.name,
                "year_level": fee.student.s_lvl,
                "set": fee.student.s_set or NO_SET,
                "total_amount": money(fee.total_amount),
                "paid_before_waive": money(
                    fee.total_amount - fee.discount_total - fee.balance
                ),
                "discounted_before_waive": money(fee.discount_total),
                "waived_amount": money(fee.balance),
                # The waive flow requires remarks, so this is the reason.
                "remarks": fee.remarks or None,
                "payments": [
                    {
                        **_payment_row(payment),
                        "status": payment.status,
                        "submission_reference": (
                            payment.payment_submission.reference_number
                            if payment.payment_submission_id
                            else None
                        ),
                    }
                    for payment in payments_by_fee.get(fee.pk, [])
                ],
                "submission_items": [
                    {
                        "item_id": item.pk,
                        "payment_submission_id": item.payment_submission_id,
                        "submission_reference": (
                            item.payment_submission.reference_number
                        ),
                        "submission_status": item.payment_submission.status,
                        "previous_balance": money(item.previous_balance),
                        "amount_paid": money(item.amount_paid),
                        "submitted_at": (
                            item.payment_submission.created_at.isoformat()
                        ),
                    }
                    for item in items_by_fee.get(fee.pk, [])
                ],
            }
        )

    return {
        "count": len(records),
        "billed": money(billed),
        "collected_before_waive": money(collected),
        "discounted_before_waive": money(discounted),
        "waived_amount": money(waived_amount),
        "records": records,
    }


def _submissions_section(
    submissions: list[PaymentSubmission],
    items: list[PaymentSubmissionItems],
) -> dict[str, Any]:
    """One record per submission - never repeated per fee - plus totals."""
    item_counts = Counter(item.payment_submission_id for item in items)

    records = []
    total = 0.0
    for submission in submissions:
        total += float(submission.total_amount_paid)
        records.append(
            {
                "id": submission.pk,
                "submitted_at": submission.created_at.isoformat(),
                "student_number": submission.student.s_studentID,
                "student_name": student_name(submission.student),
                "reference_number": submission.reference_number,
                "status": submission.status,
                "total_amount_paid": money(submission.total_amount_paid),
                # How many fees the submission pays off - its items live in
                # the payment_submission_items section, joined by this id.
                "fee_count": item_counts.get(submission.pk, 0),
                "screenshot_urls": _screenshot_urls(submission),
                "reviewed_by": (
                    submission.reviewed_by.username
                    if submission.reviewed_by
                    else None
                ),
                "reviewed_at": (
                    submission.reviewed_at.isoformat()
                    if submission.reviewed_at
                    else None
                ),
                "remarks": submission.remarks or None,
                "updated_at": submission.updated_at.isoformat(),
            }
        )

    return {
        "records": records,
        "totals": {
            "count": len(records),
            "total_amount_paid": money(total),
        },
    }


def _items_section(items: list[PaymentSubmissionItems]) -> dict[str, Any]:
    """One record per submission line item - a fee's slice of a submission."""
    records = []
    total = 0.0
    for item in items:
        total += float(item.amount_paid)
        records.append(
            {
                "id": item.pk,
                "payment_submission_id": item.payment_submission_id,
                "submission_reference": item.payment_submission.reference_number,
                "submission_status": item.payment_submission.status,
                "fee_id": item.fee_id,
                "category": item.fee.category.category_name,
                "student_number": item.fee.student.s_studentID,
                "student_name": student_name(item.fee.student),
                "previous_balance": money(item.previous_balance),
                "amount_paid": money(item.amount_paid),
                "created_at": item.created_at.isoformat(),
            }
        )

    return {
        "records": records,
        "totals": {
            "count": len(records),
            "amount_paid": money(total),
        },
    }


def _detailed_fee(
    fee: Fee,
    fee_items: dict[int, list[PaymentSubmissionItems]],
    linked: dict[tuple[int, int], list[Payment]],
    direct: dict[int, list[Payment]],
) -> dict[str, Any]:
    submissions = []
    for item in fee_items.get(fee.pk, []):
        submission = item.payment_submission
        submissions.append(
            {
                "id": submission.pk,
                "reference_number": submission.reference_number,
                "status": submission.status,
                # This fee's slice of the submission vs the whole submission,
                # which may be paying off other fees too.
                "amount_for_this_fee": money(item.amount_paid),
                "previous_balance_for_this_fee": money(item.previous_balance),
                "total_amount_paid": money(submission.total_amount_paid),
                "submitted_at": submission.created_at.isoformat(),
                "screenshot_urls": _screenshot_urls(submission),
                "remarks": submission.remarks or None,
                "reviewed_by": (
                    submission.reviewed_by.username
                    if submission.reviewed_by
                    else None
                ),
                "reviewed_at": (
                    submission.reviewed_at.isoformat()
                    if submission.reviewed_at
                    else None
                ),
                "payments": [
                    _payment_row(payment)
                    for payment in linked.get((fee.pk, submission.pk), [])
                ],
            }
        )

    return {
        "id": fee.pk,
        "student_number": fee.student.s_studentID,
        "student_name": student_name(fee.student),
        "program": fee.student.program.name,
        "year_level": fee.student.s_lvl,
        "set": fee.student.s_set or NO_SET,
        "total_amount": money(fee.total_amount),
        "discount_total": money(fee.discount_total),
        "paid": money(fee.total_amount - fee.discount_total - fee.balance),
        "balance": money(fee.balance),
        "status": fee.status,
        "due_date": fee.due_date.isoformat() if fee.due_date else None,
        "issued_at": fee.created_at.isoformat(),
        "issued_by": fee.issued_by.username if fee.issued_by else None,
        "academic_year": fee.academic_year,
        "semester": fee.semester,
        # For a waived fee this is the exemption reason.
        "remarks": fee.remarks or None,
        "submissions": submissions,
        "payments": [_payment_row(payment) for payment in direct.get(fee.pk, [])],
    }


def _payment_row(payment: Payment) -> dict[str, Any]:
    return {
        "id": payment.pk,
        "amount_paid": money(payment.amount_paid),
        "previous_balance": money(payment.previous_balance),
        "payment_method": payment.payment_method,
        "received_by": (
            payment.received_by.username if payment.received_by else None
        ),
        "created_at": payment.created_at.isoformat(),
    }


def _detailed_summary(fees, payments, submissions) -> dict[str, Any]:
    """
    The headline block above the fee list.

    Deliberately computed from the very rows listed below it - every payment
    attached to a listed fee, every submission linked to one - rather than
    re-queried by date. The summary of a list that did not add up to the list
    would be worse than no summary.
    """
    status_breakdown = {"paid": 0, "pending": 0, "overdue": 0, "waived": 0}
    for fee in fees:
        if fee.status in status_breakdown:
            status_breakdown[fee.status] += 1

    cash = sum(
        float(p.amount_paid)
        for p in payments
        if p.payment_method == Payment.PaymentMethod.CASH
    )
    gcash = sum(
        float(p.amount_paid)
        for p in payments
        if p.payment_method == Payment.PaymentMethod.GCASH
    )

    stats = {
        "total_count": 0,
        "total_amount": 0.0,
        "approved_count": 0,
        "approved_amount": 0.0,
        "pending_count": 0,
        "pending_amount": 0.0,
        "rejected_count": 0,
        "rejected_amount": 0.0,
        "cancelled_count": 0,
        "cancelled_amount": 0.0,
        "withdrawn_count": 0,
        "withdrawn_amount": 0.0,
    }
    for submission in submissions:
        stats["total_count"] += 1
        stats["total_amount"] += float(submission.total_amount_paid)
        stats[f"{submission.status}_count"] += 1
        stats[f"{submission.status}_amount"] += float(submission.total_amount_paid)

    return {
        "status_breakdown": status_breakdown,
        "payments": {
            "total_collections": money(cash + gcash),
            "cash": money(cash),
            "gcash": money(gcash),
            "tx_count": len(payments),
        },
        "payment_submissions": submission_section(stats),
    }


# ==========================================================================
# By staff
# ==========================================================================


def build_staff_report(*, user, filters: ReportFilters) -> dict[str, Any]:
    """
    Every payment each staff member received, grouped by staff.

    "Handled" means *received* - submissions a staff member merely reviewed
    are not their collections. Payments with no receiver recorded are left
    out entirely; they belong to nobody's tally.
    """
    institute_id = institute_scope(user)

    payments = list(
        selectors.payment_queryset(filters=filters, institute_id=institute_id)
        .exclude(received_by__isnull=True)
        .select_related(
            "received_by",
            "payment_submission",
            "fee__student",
            "fee__category",
        )
        .order_by("-created_at", "-pk")
    )

    groups: dict[int, dict[str, Any]] = {}
    for payment in payments:
        group = groups.setdefault(
            payment.received_by_id,
            {
                "user": payment.received_by,
                "collected": 0.0,
                "cash": 0.0,
                "gcash": 0.0,
                "payments": [],
            },
        )
        amount = float(payment.amount_paid)
        group["collected"] += amount
        if payment.payment_method == Payment.PaymentMethod.CASH:
            group["cash"] += amount
        elif payment.payment_method == Payment.PaymentMethod.GCASH:
            group["gcash"] += amount
        group["payments"].append(
            {
                "id": payment.pk,
                "created_at": payment.created_at.isoformat(),
                "amount_paid": money(payment.amount_paid),
                "previous_balance": money(payment.previous_balance),
                "payment_method": payment.payment_method,
                "student_number": payment.fee.student.s_studentID,
                "student_name": student_name(payment.fee.student),
                "category": payment.fee.category.category_name,
                "fee_id": payment.fee_id,
                "submission_reference": (
                    payment.payment_submission.reference_number
                    if payment.payment_submission_id
                    else None
                ),
            }
        )

    grand_collected = sum(group["collected"] for group in groups.values())
    grand_cash = sum(group["cash"] for group in groups.values())
    grand_gcash = sum(group["gcash"] for group in groups.values())

    staff = [
        {
            "staff_username": group["user"].username,
            "full_name": group["user"].get_full_name() or None,
            "totals": {
                "collected": money(group["collected"]),
                "tx_count": len(group["payments"]),
                "cash": money(group["cash"]),
                "gcash": money(group["gcash"]),
                "share_of_total": rate(group["collected"], grand_collected),
            },
            "payments": group["payments"],
        }
        for group in sorted(
            groups.values(), key=lambda g: -g["collected"]
        )
    ]

    return {
        "generated_at": timezone.now().isoformat(),
        "staff": staff,
        "totals": {
            "collected": money(grand_collected),
            "tx_count": len(payments),
            "cash": money(grand_cash),
            "gcash": money(grand_gcash),
        },
    }


# ==========================================================================
# By program, year level and set
# ==========================================================================


def build_program_set_report(*, user, filters: ReportFilters) -> dict[str, Any]:
    """
    Program -> year level -> set -> student -> fees, with the same totals
    shape at every level.

    Students appear only if they have a fee in the window - a roster is a
    different report. Ordering is fixed: program A-Z, year level ascending,
    set A-Z with blank sets last, students by surname, fees by category.
    """
    institute_id = institute_scope(user)

    fees = list(
        selectors.fee_queryset(filters=filters, institute_id=institute_id)
        .select_related("student__program", "category")
    )

    # program name -> year level -> set -> student pk -> (student, fees)
    tree: dict[str, dict[int, dict[str, dict[int, dict[str, Any]]]]] = (
        defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    )
    for fee in fees:
        student = fee.student
        leaf = tree[student.program.name][student.s_lvl][
            student.s_set or NO_SET
        ].setdefault(student.pk, {"student": student, "fees": []})
        leaf["fees"].append(fee)

    grand = FeeTotals()
    grand_students = 0
    programs = []

    for program_name in sorted(tree):
        program_totals = FeeTotals()
        program_students = 0
        year_levels = []

        for level in sorted(tree[program_name]):
            level_totals = FeeTotals()
            level_students = 0
            sets = []

            for set_name in sorted(
                tree[program_name][level], key=lambda s: (s == NO_SET, s)
            ):
                set_totals = FeeTotals()
                students = []

                for leaf in sorted(
                    tree[program_name][level][set_name].values(),
                    key=lambda leaf: (
                        leaf["student"].s_lname,
                        leaf["student"].s_fname,
                        leaf["student"].s_studentID,
                    ),
                ):
                    student = leaf["student"]
                    student_totals = FeeTotals()
                    fee_rows = []

                    for fee in sorted(
                        leaf["fees"],
                        key=lambda f: (f.category.category_name, f.pk),
                    ):
                        # The same fee feeds every ancestor's totals, which is
                        # what makes each level sum exactly to its parent.
                        for totals in (
                            student_totals,
                            set_totals,
                            level_totals,
                            program_totals,
                            grand,
                        ):
                            totals.add(fee)
                        fee_rows.append(
                            {
                                "id": fee.pk,
                                "category": fee.category.category_name,
                                "total_amount": money(fee.total_amount),
                                "discount_total": money(fee.discount_total),
                                "paid": money(
                                    fee.total_amount
                                    - fee.discount_total
                                    - fee.balance
                                ),
                                "balance": money(fee.balance),
                                "status": fee.status,
                                "due_date": (
                                    fee.due_date.isoformat()
                                    if fee.due_date
                                    else None
                                ),
                                "remarks": fee.remarks or None,
                            }
                        )

                    students.append(
                        {
                            "student_number": student.s_studentID,
                            "student_name": student_name(student),
                            "totals": student_totals.shape(),
                            "fees": fee_rows,
                        }
                    )

                set_students = len(students)
                level_students += set_students
                sets.append(
                    {
                        "set": set_name,
                        "totals": set_totals.shape(student_count=set_students),
                        "students": students,
                    }
                )

            program_students += level_students
            year_levels.append(
                {
                    "year_level": level,
                    "totals": level_totals.shape(student_count=level_students),
                    "sets": sets,
                }
            )

        grand_students += program_students
        programs.append(
            {
                "program": program_name,
                "totals": program_totals.shape(student_count=program_students),
                "year_levels": year_levels,
            }
        )

    return {
        "generated_at": timezone.now().isoformat(),
        "programs": programs,
        "totals": grand.shape(student_count=grand_students),
    }
