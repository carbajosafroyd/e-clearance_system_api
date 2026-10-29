"""
Discounted fees, and the report that used to call them theft.

The collection report reconciles what a fee says it drew down against the
payments behind it, and treats an unexplained drawdown as *"revenue given
away unrecorded"*. Relief is a drawdown with no payment behind it, so unless
the reconciliation subtracts it, every discount books itself as a loss and
the metric that exists to catch a hand-edited balance is buried under
legitimate ones.

These tests exist because that failure is silent: the report does not crash,
it just reports a plausible, wrong number.
"""

from decimal import Decimal

from django.test import TestCase

from api.fees.models import Fee
from api.fees.tests.factories import (
    ACADEMIC_YEAR,
    SEMESTER,
    make_category,
    make_fee,
    make_institute,
    make_program,
    make_staff,
    make_student,
)
from api.payments.models import Payment

from ..periods import ReportFilters
from ..services import build_collection_report


def pay(fee, amount, *, by=None):
    """Record a payment and draw the fee's balance down by it."""
    Payment.objects.create(
        fee=fee,
        amount_paid=Decimal(amount),
        previous_balance=fee.balance,
        payment_method=Payment.PaymentMethod.CASH,
        received_by=by,
    )
    fee.balance = fee.balance - Decimal(amount)
    if fee.balance <= 0:
        fee.status = Fee.FeeStatus.PAID
    fee.save(update_fields=["balance", "status"])


def discount(fee: Fee, amount: str) -> Fee:
    """
    Apply relief the way the discount service does — raise `discount_total`,
    lower `balance`, leave `total_amount` alone.

    Written out rather than calling `approve_discount` on purpose: this is
    the *shape* the reports must cope with, and it has to hold for a coupon
    or anything else that grants relief later, not only for points.
    """
    value = Decimal(amount)
    fee.discount_total += value
    fee.balance -= value
    if fee.balance <= Decimal("0.00"):
        fee.balance = Decimal("0.00")
        fee.status = Fee.FeeStatus.PAID
    fee.save(update_fields=["discount_total", "balance", "status"])
    return fee


class DiscountReconciliationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.cashier = make_staff(cls.institute)
        cls.category = make_category(cls.institute, name="Org Dues")
        cls.student = make_student(cls.program)

    def report(self):
        return build_collection_report(
            user=self.staff,
            filters=ReportFilters(academic_year=ACADEMIC_YEAR, semester=SEMESTER),
        )

    # -- the regression this file is named after ----------------------------

    def test_a_discount_is_not_a_shortfall(self):
        """
        Relief is recorded on the fee, so the reconciliation must not read
        it as money that went missing. It is still revenue given away, and
        the losses section says so on its own line - `discounts`, counted
        into the total - rather than in the shortfall, which is reserved for
        drawdowns nothing explains.
        """
        fee = make_fee(self.student, self.category, amount="500.00")
        discount(fee, "100.00")
        pay(fee, "400.00", by=self.cashier)

        losses = self.report()["losses"]

        self.assertEqual(losses["discrepancies"]["count"], 0)
        self.assertEqual(losses["discrepancies"]["shortfall"], 0.0)
        self.assertEqual(losses["discounts"]["count"], 1)
        self.assertEqual(losses["discounts"]["amount"], 100.0)
        self.assertEqual(losses["total_losses"], 100.0)

    def test_a_fee_cleared_entirely_by_relief_is_not_a_shortfall(self):
        fee = make_fee(self.student, self.category, amount="250.00")
        discount(fee, "250.00")

        losses = self.report()["losses"]

        self.assertEqual(losses["discrepancies"]["count"], 0)
        self.assertEqual(losses["discounts"]["amount"], 250.0)
        self.assertEqual(losses["total_losses"], 250.0)

    def test_relief_is_split_by_what_funded_it(self):
        """
        `discount()` above writes straight onto the fee, the way a hand edit
        or a backfill would, so neither ledger explains it: it lands under
        `other`, and the record says so instead of guessing a ledger.
        """
        fee = make_fee(self.student, self.category, amount="500.00")
        discount(fee, "100.00")

        record = self.report()["losses"]["discounts"]["records"][0]

        self.assertEqual(record["fee_id"], fee.pk)
        self.assertEqual(record["discount_total"], 100.0)
        self.assertEqual(record["points"], 0.0)
        self.assertEqual(record["coupons"], 0.0)
        self.assertEqual(record["other"], 100.0)

    def test_a_hand_edited_balance_is_still_caught(self):
        """
        The detector has to keep working. A balance written down without
        either a payment or recorded relief behind it is exactly what it is
        for — and this case is now *more* visible than it was, because
        relief has somewhere of its own to be.
        """
        fee = make_fee(self.student, self.category, amount="500.00")
        Fee.objects.filter(pk=fee.pk).update(balance=Decimal("400.00"))

        losses = self.report()["losses"]

        self.assertEqual(losses["discrepancies"]["count"], 1)
        self.assertEqual(losses["discrepancies"]["shortfall"], 100.0)
        self.assertEqual(losses["total_losses"], 100.0)

    # -- the summary figures ------------------------------------------------

    def test_relief_lowers_what_is_expected_and_not_what_came_in(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        discount(fee, "100.00")
        pay(fee, "150.00", by=self.cashier)

        report = self.report()
        row = next(
            r for r in report["categories"] if r["category_name"] == "Org Dues"
        )

        self.assertEqual(row["billed"], 500.0)
        self.assertEqual(row["discounts"], 100.0)
        self.assertEqual(row["expected"], 400.0)
        self.assertEqual(row["collected"], 150.0)
        self.assertEqual(row["outstanding"], 250.0)

    def test_the_category_identities_hold(self):
        """billed - discounts == expected, and expected - collected == owed."""
        first = make_fee(self.student, self.category, amount="500.00")
        discount(first, "50.00")
        pay(first, "200.00", by=self.cashier)

        other = make_student(self.program)
        second = make_fee(other, self.category, amount="300.00")
        pay(second, "300.00", by=self.cashier)

        row = next(
            r for r in self.report()["categories"] if r["category_name"] == "Org Dues"
        )

        self.assertEqual(row["billed"] - row["discounts"], row["expected"])
        self.assertEqual(row["expected"] - row["collected"], row["outstanding"])

    # -- waived fees --------------------------------------------------------

    def test_relief_on_a_waived_fee_is_not_counted_as_money_paid(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        discount(fee, "100.00")
        pay(fee, "150.00", by=self.cashier)
        fee.status = Fee.FeeStatus.WAIVED
        fee.remarks = "Hardship"
        fee.save(update_fields=["status", "remarks"])

        waived = self.report()["losses"]["waived"]

        self.assertEqual(waived["billed"], 500.0)
        self.assertEqual(waived["collected_before_waive"], 150.0)
        self.assertEqual(waived["discounted_before_waive"], 100.0)
        self.assertEqual(waived["waived_amount"], 250.0)

        record = waived["records"][0]
        self.assertEqual(record["paid_before_waive"], 150.0)
        self.assertEqual(record["discounted_before_waive"], 100.0)


class NoReductionSchemeTests(TestCase):
    """
    The deployment that runs no reduction scheme at all — every fee has
    `discount_total` of zero, and every figure must be exactly what it was
    before the column existed.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.cashier = make_staff(cls.institute)
        cls.category = make_category(cls.institute, name="Tuition")
        cls.student = make_student(cls.program)

    def test_the_numbers_are_untouched_when_nothing_is_discounted(self):
        fee = make_fee(self.student, self.category, amount="1000.00")
        pay(fee, "600.00", by=self.cashier)

        report = build_collection_report(
            user=self.staff,
            filters=ReportFilters(academic_year=ACADEMIC_YEAR, semester=SEMESTER),
        )
        row = next(r for r in report["categories"] if r["category_name"] == "Tuition")

        self.assertEqual(row["billed"], 1000.0)
        self.assertEqual(row["discounts"], 0.0)
        self.assertEqual(row["expected"], 1000.0)
        self.assertEqual(row["collected"], 600.0)
        self.assertEqual(row["outstanding"], 400.0)
        self.assertEqual(report["losses"]["total_losses"], 0.0)
