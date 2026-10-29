"""
Shaping shared by every report service.

Everything here is arithmetic and formatting - no queries. The rules live in
one place so the collection report and the detailed reports cannot drift on
questions like "how is a rate rounded" or "does a waived fee count".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from api.fees.models import Fee

from .. import selectors


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


class FeeTotals:
    """
    billed / paid / balance accumulated over a run of fees.

    Waived fees are skipped entirely: they keep their full amount in
    `balance` in this codebase, so summing them would invent debt nobody owes
    and drag every collection rate down for money never expected. Status
    *counts* are a different question and are handled elsewhere.

    Every level of every detailed report - student, set, year level, program,
    category, grand total - uses this one class, which is what makes the
    child totals sum exactly to their parents': they are all fed the same
    fees through the same rule.
    """

    __slots__ = ("billed", "discounts", "paid", "balance")

    def __init__(self) -> None:
        self.billed = 0.0
        self.discounts = 0.0
        self.paid = 0.0
        self.balance = 0.0

    def add(self, fee: Fee) -> None:
        if fee.status == Fee.FeeStatus.WAIVED:
            return
        self.billed += float(fee.total_amount)
        self.discounts += float(fee.discount_total)
        self.paid += float(fee.total_amount - fee.discount_total - fee.balance)
        self.balance += float(fee.balance)

    def shape(self, *, student_count: Optional[int] = None) -> dict[str, Any]:
        data = {
            "billed": money(self.billed),
            "discounts": money(self.discounts),
            "expected": money(self.billed - self.discounts),
            "paid": money(self.paid),
            "balance": money(self.balance),
            # Against what was actually asked for, not what was billed. A
            # programme that collected everything it charged after granting
            # relief collected 100% of it, and rating that 90% penalises the
            # coordinator for running the scheme they were told to run.
            "collection_rate": rate(self.paid, self.billed - self.discounts),
        }
        if student_count is not None:
            data["student_count"] = student_count
        return data


def export_filename(*, prefix: str, extension: str, now=None) -> str:
    """`collection-report-20260816-0233.pdf` - any report, any format."""
    from django.utils import timezone

    from ..constants import EXPORT_FILENAME, EXPORT_STAMP_FORMAT

    stamp = timezone.localtime(now or timezone.now()).strftime(EXPORT_STAMP_FORMAT)
    return EXPORT_FILENAME.format(prefix=prefix, stamp=stamp, extension=extension)


def institute_scope(user) -> Optional[int]:
    """
    Which institute a report is limited to.

    Staff see their own institute and nothing else - the reports are
    institute-wide, so this is the line that stops one institute reading
    another's collections.

    None means no limit at all, and only a superuser gets that. A staff
    account with *no* institute attached is scoped to `NO_INSTITUTE`, i.e.
    nothing, rather than being handed every institute's figures by accident.
    """
    institute_id = getattr(user, "institute_id", None)
    if institute_id is not None:
        return institute_id
    if getattr(user, "is_superuser", False):
        return None
    return selectors.NO_INSTITUTE


def submission_section(stats: dict[str, Any]) -> dict[str, Any]:
    """
    Student-submitted payment proofs, by status.

    `total` counts and sums *every* submission whatever its status, so the
    three statuses below it always add back up to it - approved money and
    rejected money are different questions, and a total that quietly meant
    "approved only" would be read as neither.

    `approval_rate` is the share of *reviewed* submissions that were
    approved, so a backlog of unreviewed ones does not drag it down. It is
    null - not 0 - when nothing has been reviewed yet, which keeps "nobody
    has looked at these" distinct from "every one was rejected".

    Cancelled submissions (approved, later undone) are their own bucket.
    They were approved at review time, so they still count as approvals in
    `approval_rate` - the rate measures the review decision, not what
    happened to the money afterwards.

    Withdrawn submissions (recalled by the student before review) sit
    outside `approval_rate` entirely, like pending ones - nobody ever made
    a review decision about them.
    """
    reviewed_approved = stats["approved_count"] + stats["cancelled_count"]
    decided = reviewed_approved + stats["rejected_count"]

    return {
        "total": {
            "count": stats["total_count"],
            "amount": money(stats["total_amount"]),
        },
        "approved": {
            "count": stats["approved_count"],
            "amount": money(stats["approved_amount"]),
        },
        "pending": {
            "count": stats["pending_count"],
            "amount": money(stats["pending_amount"]),
        },
        "rejected": {
            "count": stats["rejected_count"],
            "amount": money(stats["rejected_amount"]),
        },
        "cancelled": {
            "count": stats["cancelled_count"],
            "amount": money(stats["cancelled_amount"]),
        },
        "withdrawn": {
            "count": stats["withdrawn_count"],
            "amount": money(stats["withdrawn_amount"]),
        },
        "approval_rate": (
            rate(reviewed_approved, decided) if decided else None
        ),
    }
