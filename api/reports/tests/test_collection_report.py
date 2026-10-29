from datetime import date, datetime, time
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

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
from api.payment_submissions.models import (
    PaymentSubmission,
    PaymentSubmissionItems,
)
from api.payments.models import Payment

from ..periods import ReportFilters
from ..services import build_collection_report


def pay(fee, amount, *, method=Payment.PaymentMethod.CASH, by=None):
    """Record a payment and draw the fee's balance down by it."""
    payment = Payment.objects.create(
        fee=fee,
        amount_paid=Decimal(amount),
        previous_balance=fee.balance,
        payment_method=method,
        received_by=by,
    )
    fee.balance = fee.balance - Decimal(amount)
    if fee.balance <= 0:
        fee.status = Fee.FeeStatus.PAID
    fee.save(update_fields=["balance", "status"])
    return payment


class CollectionReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.cashier = make_staff(cls.institute)

        cls.tuition = make_category(cls.institute, name="Tuition Fee")
        cls.misc = make_category(cls.institute, name="Library Fee")

        cls.student = make_student(cls.program)
        cls.student.s_set = "A"
        cls.student.save(update_fields=["s_set"])

        # 100 billed, 60 collected.
        cls.paid_off = make_fee(cls.student, cls.tuition, amount="100.00")
        pay(cls.paid_off, "60.00", by=cls.cashier)

        # 200 billed, nothing collected, and it has gone overdue.
        cls.overdue = make_fee(
            cls.student, cls.misc, amount="200.00", status=Fee.FeeStatus.OVERDUE
        )

        # Waived: carries a full balance it will never pay. Must not count as
        # money owed, and must not drag the collection rate down.
        cls.waived = make_fee(
            cls.student, cls.misc, amount="500.00", status=Fee.FeeStatus.WAIVED
        )

        pay(cls.paid_off, "10.00", method=Payment.PaymentMethod.GCASH, by=cls.cashier)

    def report(self, **overrides):
        filters = ReportFilters(
            academic_year=ACADEMIC_YEAR, semester=SEMESTER, **overrides
        )
        return build_collection_report(user=self.staff, filters=filters)

    def test_kpi_totals_are_split_by_payment_method(self):
        kpi = self.report()["kpi"]

        self.assertEqual(kpi["total_collections"], 70.0)
        self.assertEqual(kpi["cash"], 60.0)
        self.assertEqual(kpi["gcash"], 10.0)

    def test_waived_fees_are_not_counted_as_outstanding(self):
        kpi = self.report()["kpi"]

        # 30 still owed on the tuition fee + 200 on the overdue one. The
        # waived 500 is excluded even though its balance is untouched.
        self.assertEqual(kpi["total_outstanding"], 230.0)

    def test_status_breakdown_counts_every_status(self):
        breakdown = self.report()["status_breakdown"]

        self.assertEqual(
            breakdown, {"paid": 0, "pending": 1, "overdue": 1, "waived": 1}
        )

    def test_category_rates_ignore_waived_fees(self):
        categories = {
            row["category_name"]: row for row in self.report()["categories"]
        }

        # 70 of 100 collected.
        self.assertEqual(categories["Tuition Fee"]["collection_rate"], 70.0)
        # The 200 overdue fee alone - the waived 500 is not in the denominator.
        self.assertEqual(categories["Library Fee"]["collection_rate"], 0.0)
        self.assertEqual(categories["Library Fee"]["expected"], 200.0)
        self.assertEqual(categories["Library Fee"]["outstanding"], 200.0)

    def test_every_category_carries_a_count_for_every_status(self):
        categories = {
            row["category_name"]: row for row in self.report()["categories"]
        }

        self.assertEqual(
            {
                key: categories["Library Fee"][key]
                for key in (
                    "fee_count",
                    "paid_count",
                    "pending_count",
                    "overdue_count",
                    "waived_count",
                )
            },
            {
                # The waived fee is counted here even though it is kept out of
                # every money column above.
                "fee_count": 2,
                "paid_count": 0,
                "pending_count": 0,
                "overdue_count": 1,
                "waived_count": 1,
            },
        )
        self.assertEqual(categories["Tuition Fee"]["pending_count"], 1)
        self.assertEqual(categories["Tuition Fee"]["waived_count"], 0)

    def test_the_status_counts_agree_with_the_overall_breakdown(self):
        report = self.report()

        for status in ("paid", "pending", "overdue", "waived"):
            self.assertEqual(
                sum(row[f"{status}_count"] for row in report["categories"]),
                report["status_breakdown"][status],
                f"{status} counts disagree between the two sections",
            )

    def test_top_category_is_the_best_rate(self):
        kpi = self.report()["kpi"]

        self.assertEqual(kpi["top_category"], "Tuition Fee")
        self.assertEqual(kpi["top_category_rate"], 70.0)

    def test_staff_rows_carry_transaction_counts(self):
        staff = self.report()["staff"]

        self.assertEqual(len(staff), 1)
        self.assertEqual(staff[0]["staff_username"], self.cashier.username)
        self.assertEqual(staff[0]["tx_count"], 2)
        self.assertEqual(staff[0]["collected"], 70.0)

    def test_filter_options_list_programs_and_staff(self):
        options = self.report()["filter_options"]

        self.assertIn(self.program.name, options["programs"])
        self.assertEqual(options["staff"], [self.cashier.username])

    def test_program_sets_report_every_money_column(self):
        rows = self.report()["program_sets"]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["program"], self.program.name)
        self.assertEqual(rows[0]["year_level"], 1)
        self.assertEqual(rows[0]["set"], "A")
        self.assertEqual(rows[0]["billed"], 300.0)
        self.assertEqual(rows[0]["total"], 70.0)
        self.assertEqual(rows[0]["balance"], 230.0)
        # 70 collected of 300 billable.
        self.assertEqual(rows[0]["rate"], 23.3)

    def test_trend_is_one_series_per_real_category(self):
        trend = self.report()["trend"]

        # No hardcoded buckets - only categories money actually landed in,
        # under their real names.
        self.assertEqual(len(trend["labels"]), 1)
        self.assertEqual(
            trend["series"], [{"name": "Tuition Fee", "points": [70.0]}]
        )

    def test_both_monthly_series_share_one_axis(self):
        report = self.report()

        self.assertEqual(
            report["trend"]["labels"],
            [row["period"] for row in report["payment_methods"]],
        )
        self.assertEqual(report["payment_methods"][0]["cash"], 60.0)
        self.assertEqual(report["payment_methods"][0]["gcash"], 10.0)

    def test_previous_total_is_null_unless_asked_for(self):
        self.assertIsNone(self.report()["kpi"]["previous_total_collections"])

    def test_previous_total_is_zero_when_the_earlier_window_is_empty(self):
        kpi = self.report(compare_previous=True)["kpi"]

        # There is a comparable window (last year's 2nd semester), it just has
        # no payments in it - which is 0.0, not null.
        self.assertEqual(kpi["previous_total_collections"], 0.0)

    def test_program_filter_narrows_the_report(self):
        empty = self.report(program_name="Nothing Named This")

        self.assertEqual(empty["kpi"]["total_collections"], 0.0)
        self.assertEqual(empty["categories"], [])

    def test_year_level_filter_narrows_the_report(self):
        self.assertEqual(self.report(year_level=1)["kpi"]["total_collections"], 70.0)
        self.assertEqual(self.report(year_level=4)["kpi"]["total_collections"], 0.0)


class LossesSectionTests(TestCase):
    """
    The losses section: waived fees as forgiven money, and the per-fee
    reconciliation of the fee's arithmetic against its payments.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.cashier = make_staff(cls.institute)
        cls.category = make_category(cls.institute, name="Locker Fee")
        cls.student = make_student(cls.program)

    def report(self):
        return build_collection_report(
            user=self.staff,
            filters=ReportFilters(academic_year=ACADEMIC_YEAR, semester=SEMESTER),
        )

    def waive(self, fee, remarks="Working scholar"):
        fee.status = Fee.FeeStatus.WAIVED
        fee.remarks = remarks
        fee.save(update_fields=["status", "remarks"])
        return fee

    def test_a_waived_fee_loses_only_its_forgiven_remainder(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        pay(fee, "200.00", by=self.cashier)
        self.waive(fee, remarks="Hardship")

        losses = self.report()["losses"]

        waived = losses["waived"]
        self.assertEqual(waived["count"], 1)
        self.assertEqual(waived["billed"], 500.0)
        self.assertEqual(waived["collected_before_waive"], 200.0)
        self.assertEqual(waived["waived_amount"], 300.0)
        self.assertEqual(losses["total_losses"], 300.0)

        record = waived["records"][0]
        self.assertEqual(record["fee_id"], fee.pk)
        self.assertEqual(record["category"], "Locker Fee")
        self.assertEqual(record["total_amount"], 500.0)
        self.assertEqual(record["paid_before_waive"], 200.0)
        self.assertEqual(record["waived_amount"], 300.0)
        self.assertEqual(record["remarks"], "Hardship")

        # Its 200 in payments matches its 200 paid off - no discrepancy.
        self.assertEqual(losses["discrepancies"]["count"], 0)

    def test_money_on_a_waived_fee_leaves_every_collection_figure(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        pay(fee, "200.00", by=self.cashier)
        self.waive(fee)

        report = self.report()

        # The 200 is out of the KPI, the method split, the staff tally and
        # the monthly axis - it lives in losses.collected_before_waive now.
        self.assertEqual(report["kpi"]["total_collections"], 0.0)
        self.assertEqual(report["kpi"]["cash"], 0.0)
        self.assertEqual(report["staff"], [])
        self.assertEqual(report["payment_methods"], [])
        category = report["categories"][0]
        self.assertEqual(category["expected"], 0.0)
        self.assertEqual(category["collected"], 0.0)
        self.assertEqual(category["waived_count"], 1)

    def test_a_fee_that_disagrees_with_its_payments_is_flagged(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        # A hand-edited balance with no payment behind it.
        fee.balance = Decimal("40.00")
        fee.save(update_fields=["balance"])

        losses = self.report()["losses"]

        discrepancies = losses["discrepancies"]
        self.assertEqual(discrepancies["count"], 1)
        self.assertEqual(discrepancies["difference"], 60.0)
        self.assertEqual(discrepancies["shortfall"], 60.0)
        record = discrepancies["records"][0]
        self.assertEqual(record["fee_id"], fee.pk)
        self.assertEqual(record["fee_paid"], 60.0)
        self.assertEqual(record["payments_total"], 0.0)
        self.assertEqual(record["difference"], 60.0)
        # The fee was drawn down with no payment behind it - money given
        # away unrecorded, so the shortfall joins the loss total.
        self.assertEqual(losses["total_losses"], 60.0)

    def test_extra_payments_are_flagged_but_are_not_a_loss(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        # A payment recorded without the fee being drawn down: the money is
        # in hand, so it is a mismatch to investigate, not missing revenue.
        pay(fee, "30.00", by=self.cashier)
        fee.balance = Decimal("100.00")
        fee.save(update_fields=["balance"])

        losses = self.report()["losses"]

        discrepancies = losses["discrepancies"]
        self.assertEqual(discrepancies["count"], 1)
        self.assertEqual(discrepancies["difference"], -30.0)
        self.assertEqual(discrepancies["shortfall"], 0.0)
        self.assertEqual(losses["total_losses"], 0.0)

    def test_an_approved_submission_without_its_payment_shows_in_the_flag(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        # The approval drew the balance down but its payment never landed.
        fee.balance = Decimal("40.00")
        fee.save(update_fields=["balance"])
        submission = PaymentSubmission.objects.create(
            student=self.student,
            screenshot=[],
            total_amount_paid=Decimal("60.00"),
            reference_number="REF-LOST",
            status=PaymentSubmission.SubmissionStatus.APPROVED,
        )
        PaymentSubmissionItems.objects.create(
            payment_submission=submission,
            fee=fee,
            previous_balance=Decimal("100.00"),
            amount_paid=Decimal("60.00"),
        )

        record = self.report()["losses"]["discrepancies"]["records"][0]

        self.assertEqual(record["payments_total"], 0.0)
        self.assertEqual(record["approved_submission_total"], 60.0)
        self.assertEqual(record["difference"], 60.0)

    def test_a_fee_that_reconciles_is_not_flagged(self):
        fee = make_fee(self.student, self.category, amount="100.00")
        pay(fee, "60.00", by=self.cashier)

        self.assertEqual(self.report()["losses"]["discrepancies"]["count"], 0)

    def test_a_fee_with_several_payments_still_reconciles(self):
        # Two payments summing to the paid-off amount: the per-fee payment
        # total must group by the fee alone, not split per payment row.
        fee = make_fee(self.student, self.category, amount="100.00")
        pay(fee, "60.00", by=self.cashier)
        pay(fee, "10.00", method=Payment.PaymentMethod.GCASH, by=self.cashier)

        self.assertEqual(self.report()["losses"]["discrepancies"]["count"], 0)


class PaymentSubmissionSectionTests(TestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.staff = make_staff(self.institute)
        self.category = make_category(self.institute, name="Tuition Fee")
        self.student = make_student(self.program)

    def submit(self, amount, status, *, fees=None):
        submission = PaymentSubmission.objects.create(
            student=self.student,
            screenshot=[],
            total_amount_paid=Decimal(amount),
            reference_number=f"REF-{amount}-{status}",
            status=status,
        )
        for fee in fees or [make_fee(self.student, self.category, amount=amount)]:
            PaymentSubmissionItems.objects.create(
                payment_submission=submission,
                fee=fee,
                previous_balance=fee.balance,
                amount_paid=Decimal(amount),
            )
        return submission

    def report(self, **overrides):
        filters = ReportFilters(
            academic_year=ACADEMIC_YEAR, semester=SEMESTER, **overrides
        )
        return build_collection_report(user=self.staff, filters=filters)

    def test_every_status_reports_a_count_and_an_amount(self):
        self.submit("800.00", PaymentSubmission.SubmissionStatus.APPROVED)
        self.submit("30.00", PaymentSubmission.SubmissionStatus.APPROVED)
        self.submit("30.00", PaymentSubmission.SubmissionStatus.REJECTED)

        section = self.report()["payment_submissions"]

        self.assertEqual(section["total"], {"count": 3, "amount": 860.0})
        self.assertEqual(section["approved"], {"count": 2, "amount": 830.0})
        self.assertEqual(section["pending"], {"count": 0, "amount": 0.0})
        self.assertEqual(section["rejected"], {"count": 1, "amount": 30.0})

    def test_the_statuses_add_back_up_to_the_total(self):
        self.submit("800.00", PaymentSubmission.SubmissionStatus.APPROVED)
        self.submit("30.00", PaymentSubmission.SubmissionStatus.REJECTED)
        self.submit("15.00", PaymentSubmission.SubmissionStatus.PENDING)

        section = self.report()["payment_submissions"]

        self.assertEqual(
            section["approved"]["amount"]
            + section["rejected"]["amount"]
            + section["pending"]["amount"],
            section["total"]["amount"],
        )

    def test_withdrawn_submissions_get_their_own_bucket(self):
        self.submit("800.00", PaymentSubmission.SubmissionStatus.APPROVED)
        self.submit("25.00", PaymentSubmission.SubmissionStatus.WITHDRAWN)

        section = self.report()["payment_submissions"]

        self.assertEqual(section["withdrawn"], {"count": 1, "amount": 25.0})
        self.assertEqual(section["total"], {"count": 2, "amount": 825.0})
        # Nobody ever reviewed a withdrawn submission, so - like a pending
        # one - it must not drag the approval rate down.
        self.assertEqual(section["approval_rate"], 100.0)

    def test_approval_rate_ignores_submissions_nobody_has_reviewed(self):
        self.submit("800.00", PaymentSubmission.SubmissionStatus.APPROVED)
        self.submit("800.00", PaymentSubmission.SubmissionStatus.APPROVED)
        self.submit("800.00", PaymentSubmission.SubmissionStatus.APPROVED)
        self.submit("30.00", PaymentSubmission.SubmissionStatus.REJECTED)
        # A review backlog is not a rejection, so it must not push the rate
        # down: 3 of 4 *reviewed*, not 3 of 6 submitted.
        self.submit("10.00", PaymentSubmission.SubmissionStatus.PENDING)
        self.submit("10.00", PaymentSubmission.SubmissionStatus.PENDING)

        self.assertEqual(self.report()["payment_submissions"]["approval_rate"], 75.0)

    def test_approval_rate_is_null_when_nothing_has_been_reviewed(self):
        self.submit("10.00", PaymentSubmission.SubmissionStatus.PENDING)

        self.assertIsNone(self.report()["payment_submissions"]["approval_rate"])

    def test_a_submission_covering_several_fees_is_only_counted_once(self):
        fees = [
            make_fee(self.student, self.category, amount="50.00"),
            make_fee(self.student, self.category, amount="50.00"),
            make_fee(self.student, self.category, amount="50.00"),
        ]
        self.submit("150.00", PaymentSubmission.SubmissionStatus.APPROVED, fees=fees)

        section = self.report()["payment_submissions"]

        # Joining through the fees returns the submission three times; the
        # amount must not triple with it.
        self.assertEqual(section["total"], {"count": 1, "amount": 150.0})

    def test_an_empty_period_still_reports_the_section(self):
        section = self.report()["payment_submissions"]

        self.assertEqual(section["total"], {"count": 0, "amount": 0.0})
        self.assertIsNone(section["approval_rate"])

    def test_submissions_stop_at_the_institute_boundary(self):
        self.submit("800.00", PaymentSubmission.SubmissionStatus.APPROVED)
        outsider = make_staff(make_institute())

        section = build_collection_report(
            user=outsider,
            filters=ReportFilters(academic_year=ACADEMIC_YEAR, semester=SEMESTER),
        )["payment_submissions"]

        self.assertEqual(section["total"], {"count": 0, "amount": 0.0})


class MonthlyAxisTests(TestCase):
    """
    The month axis, which is what the two charts are drawn along.

    The first half of 2026 is used throughout: six months, with payments
    dropped into two of them so the quiet months in between have something to
    prove.
    """

    def setUp(self):
        self.institute = make_institute()
        self.staff = make_staff(self.institute)
        program = make_program(self.institute)
        category = make_category(self.institute, name="Tuition Fee")
        self.student = make_student(program)
        self.fee = make_fee(self.student, category, amount="10000.00")

    def pay_on(self, day, amount="100.00"):
        """A payment recorded on a specific date."""
        payment = pay(self.fee, amount, by=self.staff)
        Payment.objects.filter(pk=payment.pk).update(
            created_at=timezone.make_aware(datetime.combine(day, time(12, 0)))
        )
        return payment

    def report(self, **overrides):
        return build_collection_report(
            user=self.staff, filters=ReportFilters(**overrides)
        )

    def first_half(self):
        return self.report(date_from=date(2026, 1, 1), date_to=date(2026, 6, 30))

    def test_labels_are_the_year_and_month(self):
        self.pay_on(date(2026, 2, 14))

        periods = [row["period"] for row in self.first_half()["payment_methods"]]

        self.assertEqual(
            periods,
            ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"],
        )

    def test_the_axis_spans_the_filtered_range_not_just_the_busy_months(self):
        self.pay_on(date(2026, 2, 14))

        cash = [row["cash"] for row in self.first_half()["payment_methods"]]

        # One payment, but all six months are still drawn - the quiet ones
        # are zeroes rather than missing columns.
        self.assertEqual(cash, [0.0, 100.0, 0.0, 0.0, 0.0, 0.0])

    def test_gaps_between_payments_are_filled_in(self):
        self.pay_on(date(2026, 2, 14))
        self.pay_on(date(2026, 4, 2))

        cash = [row["cash"] for row in self.first_half()["payment_methods"]]

        self.assertEqual(cash, [0.0, 100.0, 0.0, 100.0, 0.0, 0.0])

    def test_payments_in_the_same_month_are_added_up(self):
        self.pay_on(date(2026, 2, 3))
        self.pay_on(date(2026, 2, 27), amount="50.00")

        cash = [row["cash"] for row in self.first_half()["payment_methods"]]

        self.assertEqual(cash, [0.0, 150.0, 0.0, 0.0, 0.0, 0.0])

    def test_each_month_carries_its_first_and_last_day(self):
        self.pay_on(date(2026, 2, 14))

        row = self.first_half()["payment_methods"][1]

        self.assertEqual(row["period"], "2026-02")
        self.assertEqual(row["start"], "2026-02-01")
        self.assertEqual(row["end"], "2026-02-28")

    def test_the_trend_carries_the_same_months(self):
        self.pay_on(date(2026, 2, 14))
        report = self.first_half()

        trend = report["trend"]

        self.assertEqual(
            trend["months"][1],
            {"label": "2026-02", "start": "2026-02-01", "end": "2026-02-28"},
        )
        # One series, its points aligned to the axis: the quiet months are
        # zeroes, February carries the payment.
        self.assertEqual(trend["series"][0]["name"], "Tuition Fee")
        self.assertEqual(trend["series"][0]["points"], [0.0, 100.0, 0.0, 0.0, 0.0, 0.0])
        # Same axis, same order, in every monthly section.
        self.assertEqual(
            trend["labels"],
            [row["period"] for row in report["payment_methods"]],
        )

    def test_an_axis_can_cross_a_year_boundary(self):
        self.pay_on(date(2025, 12, 10))
        self.pay_on(date(2026, 1, 8))

        report = self.report(
            date_from=date(2025, 12, 1), date_to=date(2026, 1, 31)
        )

        self.assertEqual(
            [row["period"] for row in report["payment_methods"]],
            ["2025-12", "2026-01"],
        )

    def test_a_span_too_long_to_draw_falls_back_to_months_with_payments(self):
        self.pay_on(date(2020, 1, 8))
        self.pay_on(date(2026, 5, 6))

        report = self.report(
            date_from=date(2020, 1, 1), date_to=date(2026, 6, 30)
        )

        # Six and a half years of monthly columns is not a chart; only the two
        # months that saw money come back, and their labels say which.
        self.assertEqual(
            [row["period"] for row in report["payment_methods"]],
            ["2020-01", "2026-05"],
        )

    def test_a_period_with_no_payments_has_no_axis_at_all(self):
        report = self.first_half()

        self.assertEqual(report["payment_methods"], [])
        self.assertEqual(
            report["trend"], {"labels": [], "months": [], "series": []}
        )


class ProgramSetOrderingTests(TestCase):
    """program A-Z, then year level ascending, then set A-Z with blanks last."""

    def test_rows_come_back_in_reading_order(self):
        institute = make_institute()
        staff = make_staff(institute)
        category = make_category(institute, name="Tuition Fee")

        beta = make_program(institute)
        beta.name = "BS Information Technology"
        beta.save(update_fields=["name"])
        alpha = make_program(institute)
        alpha.name = "BS Computer Science"
        alpha.save(update_fields=["name"])

        # Deliberately created out of order, so only the sort can save us.
        for program, level, s_set in [
            (beta, 1, "A"),
            (alpha, 2, "B"),
            (alpha, 1, None),
            (alpha, 1, "B"),
            (alpha, 1, "A"),
        ]:
            student = make_student(program, level=level)
            student.s_set = s_set
            student.save(update_fields=["s_set"])
            make_fee(student, category, amount="100.00")

        report = build_collection_report(
            user=staff,
            filters=ReportFilters(academic_year=ACADEMIC_YEAR, semester=SEMESTER),
        )

        self.assertEqual(
            [(r["program"], r["year_level"], r["set"]) for r in report["program_sets"]],
            [
                ("BS Computer Science", 1, "A"),
                ("BS Computer Science", 1, "B"),
                ("BS Computer Science", 1, "—"),
                ("BS Computer Science", 2, "B"),
                ("BS Information Technology", 1, "A"),
            ],
        )


class InstituteScopeTests(TestCase):
    def test_a_report_stops_at_the_institute_boundary(self):
        mine = make_institute()
        theirs = make_institute()
        staff = make_staff(mine)

        for institute in (mine, theirs):
            program = make_program(institute)
            category = make_category(institute, name="Tuition Fee")
            student = make_student(program)
            fee = make_fee(student, category, amount="100.00")
            pay(fee, "100.00", by=make_staff(institute))

        report = build_collection_report(
            user=staff,
            filters=ReportFilters(academic_year=ACADEMIC_YEAR, semester=SEMESTER),
        )

        self.assertEqual(report["kpi"]["total_collections"], 100.0)

    def test_staff_without_an_institute_see_nothing_rather_than_everything(self):
        institute = make_institute()
        program = make_program(institute)
        category = make_category(institute, name="Tuition Fee")
        student = make_student(program)
        fee = make_fee(student, category, amount="100.00")
        pay(fee, "100.00", by=make_staff(institute))

        unattached = make_staff(None)

        report = build_collection_report(
            user=unattached,
            filters=ReportFilters(academic_year=ACADEMIC_YEAR, semester=SEMESTER),
        )

        self.assertEqual(report["kpi"]["total_collections"], 0.0)
        self.assertEqual(report["filter_options"]["programs"], [])

    def test_a_superuser_without_an_institute_sees_everything(self):
        first = make_institute()
        second = make_institute()
        root = make_staff(None, superuser=True)

        for institute in (first, second):
            program = make_program(institute)
            category = make_category(institute, name="Tuition Fee")
            student = make_student(program)
            fee = make_fee(student, category, amount="100.00")
            pay(fee, "100.00", by=make_staff(institute))

        report = build_collection_report(
            user=root,
            filters=ReportFilters(academic_year=ACADEMIC_YEAR, semester=SEMESTER),
        )

        self.assertEqual(report["kpi"]["total_collections"], 200.0)


class EmptyReportTests(TestCase):
    def test_a_report_with_no_data_still_has_every_section(self):
        staff = make_staff(make_institute())

        report = build_collection_report(
            user=staff, filters=ReportFilters(compare_previous=True)
        )

        self.assertEqual(report["kpi"]["total_collections"], 0.0)
        self.assertEqual(report["kpi"]["total_outstanding"], 0.0)
        self.assertIsNone(report["kpi"]["top_category"])
        # No window was selected, so there is no earlier window either.
        self.assertIsNone(report["kpi"]["previous_total_collections"])
        self.assertEqual(report["categories"], [])
        self.assertEqual(
            report["trend"], {"labels": [], "months": [], "series": []}
        )
        self.assertEqual(report["payment_methods"], [])
        self.assertEqual(
            report["status_breakdown"],
            {"paid": 0, "pending": 0, "overdue": 0, "waived": 0},
        )
        self.assertEqual(
            report["losses"],
            {
                "waived": {
                    "count": 0,
                    "billed": 0.0,
                    "collected_before_waive": 0.0,
                    "discounted_before_waive": 0.0,
                    "waived_amount": 0.0,
                    "records": [],
                },
                "discounts": {
                    "count": 0,
                    "amount": 0.0,
                    "points": 0.0,
                    "coupons": 0.0,
                    "other": 0.0,
                    "records": [],
                },
                "discrepancies": {
                    "count": 0,
                    "difference": 0.0,
                    "shortfall": 0.0,
                    "records": [],
                },
                "total_losses": 0.0,
            },
        )
