from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

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
from api.users.models import User

from ..periods import ReportFilters
from ..services import (
    build_detailed_fees_report,
    build_program_set_report,
    build_staff_report,
)

APPROVED = PaymentSubmission.SubmissionStatus.APPROVED
PENDING = PaymentSubmission.SubmissionStatus.PENDING


def pay(fee, amount, *, method=Payment.PaymentMethod.CASH, by=None, submission=None):
    """Record a payment - optionally born from a submission - and draw the fee down."""
    payment = Payment.objects.create(
        fee=fee,
        amount_paid=Decimal(amount),
        previous_balance=fee.balance,
        payment_method=method,
        received_by=by,
        payment_submission=submission,
    )
    fee.balance = fee.balance - Decimal(amount)
    if fee.balance <= 0:
        fee.status = Fee.FeeStatus.PAID
    fee.save(update_fields=["balance", "status"])
    return payment


def submit(student, fee_amounts, *, status=APPROVED, reference="REF-1", reviewed_by=None):
    """A student submission covering one or more fees."""
    submission = PaymentSubmission.objects.create(
        student=student,
        screenshot=[],
        total_amount_paid=sum(Decimal(amount) for _, amount in fee_amounts),
        reference_number=reference,
        status=status,
        reviewed_by=reviewed_by,
    )
    for fee, amount in fee_amounts:
        PaymentSubmissionItems.objects.create(
            payment_submission=submission,
            fee=fee,
            previous_balance=fee.balance,
            amount_paid=Decimal(amount),
        )
    return submission


def term_filters(**overrides):
    return ReportFilters(academic_year=ACADEMIC_YEAR, semester=SEMESTER, **overrides)


class DetailedFeesReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.cashier = make_staff(cls.institute)

        cls.tuition = make_category(cls.institute, name="Tuition Fee")
        cls.athletics = make_category(cls.institute, name="Athletics Fee")

        cls.student = make_student(cls.program)
        cls.student.s_set = "A"
        cls.student.save(update_fields=["s_set"])

        # A 500 tuition fee: 300 came in through an approved submission, 100
        # over the counter.
        cls.fee = make_fee(cls.student, cls.tuition, amount="500.00")
        cls.submission = submit(
            cls.student, [(cls.fee, "300.00")], reviewed_by=cls.cashier
        )
        cls.linked_payment = pay(
            cls.fee,
            "300.00",
            method=Payment.PaymentMethod.GCASH,
            by=cls.cashier,
            submission=cls.submission,
        )
        cls.direct_payment = pay(cls.fee, "100.00", by=cls.cashier)

        # Athletics: one untouched pending fee and one waived one.
        cls.pending_fee = make_fee(cls.student, cls.athletics, amount="200.00")
        cls.waived_fee = make_fee(
            cls.student,
            cls.athletics,
            amount="500.00",
            status=Fee.FeeStatus.WAIVED,
            remarks="Working scholar",
        )

    def report(self):
        return build_detailed_fees_report(user=self.staff, filters=term_filters())

    @staticmethod
    def find_fee(report, fee_id):
        for category in report["categories"]:
            for group in category["statuses"]:
                for fee in group["fees"]:
                    if fee["id"] == fee_id:
                        return fee
        raise AssertionError(f"fee {fee_id} not in the report")

    def test_categories_are_sorted_by_name(self):
        names = [c["category_name"] for c in self.report()["categories"]]

        self.assertEqual(names, ["Athletics Fee", "Tuition Fee"])

    def test_statuses_are_grouped_money_owed_first(self):
        athletics = self.report()["categories"][0]

        groups = {g["status"]: g for g in athletics["statuses"]}
        # Pending before waived - and each group knows its own headcount.
        self.assertEqual(
            [g["status"] for g in athletics["statuses"]], ["pending", "waived"]
        )
        self.assertEqual(groups["pending"]["count"], 1)
        self.assertEqual(groups["waived"]["count"], 1)

    def test_status_groups_sum_to_the_category_total(self):
        for category in self.report()["categories"]:
            for key in ("billed", "paid", "balance"):
                self.assertAlmostEqual(
                    sum(g["totals"][key] for g in category["statuses"]),
                    category["totals"][key],
                    msg=f"{category['category_name']} statuses do not sum on {key}",
                )

    def test_a_payment_appears_exactly_once(self):
        fee = self.find_fee(self.report(), self.fee.pk)

        # The submission-born payment lives under its submission...
        self.assertEqual(
            [p["id"] for p in fee["submissions"][0]["payments"]],
            [self.linked_payment.pk],
        )
        # ...and the counter payment under the fee. Never both places.
        self.assertEqual(
            [p["id"] for p in fee["payments"]], [self.direct_payment.pk]
        )

    def test_the_fee_carries_its_own_arithmetic(self):
        fee = self.find_fee(self.report(), self.fee.pk)

        self.assertEqual(fee["student_number"], self.student.s_studentID)
        self.assertEqual(fee["program"], self.program.name)
        self.assertEqual(fee["year_level"], 1)
        self.assertEqual(fee["set"], "A")
        self.assertEqual(fee["total_amount"], 500.0)
        self.assertEqual(fee["paid"], 400.0)
        self.assertEqual(fee["balance"], 100.0)

    def test_the_fee_carries_its_issue_and_term_details(self):
        report = self.report()

        fee = self.find_fee(report, self.fee.pk)
        self.assertEqual(fee["academic_year"], ACADEMIC_YEAR)
        self.assertEqual(fee["semester"], SEMESTER)
        self.assertIsNotNone(fee["issued_at"])
        self.assertIsNone(fee["issued_by"])  # factory fees have no issuer
        self.assertIsNone(fee["remarks"])

        # The waived fee's remarks carry the exemption reason.
        waived = self.find_fee(report, self.waived_fee.pk)
        self.assertEqual(waived["remarks"], "Working scholar")

    def test_the_submission_reports_its_slice_and_its_whole(self):
        submission = self.find_fee(self.report(), self.fee.pk)["submissions"][0]

        self.assertEqual(submission["reference_number"], "REF-1")
        self.assertEqual(submission["status"], APPROVED)
        self.assertEqual(submission["amount_for_this_fee"], 300.0)
        self.assertEqual(submission["total_amount_paid"], 300.0)
        self.assertEqual(submission["reviewed_by"], self.cashier.username)

    def test_category_totals_sum_to_the_grand_total(self):
        report = self.report()

        for key in ("billed", "paid", "balance"):
            self.assertAlmostEqual(
                sum(c["totals"][key] for c in report["categories"]),
                report["totals"][key],
            )

        athletics = report["categories"][0]["totals"]
        # The waived 500 is in neither billed nor balance.
        self.assertEqual(athletics, {
            "billed": 200.0, "discounts": 0.0, "expected": 200.0,
            "paid": 0.0, "balance": 200.0, "collection_rate": 0.0,
        })
        self.assertEqual(report["totals"]["billed"], 700.0)
        self.assertEqual(report["totals"]["collection_rate"], 57.1)

    def test_the_waived_fee_is_still_listed(self):
        waived = self.find_fee(self.report(), self.waived_fee.pk)

        self.assertEqual(waived["status"], Fee.FeeStatus.WAIVED)

    def test_the_summary_agrees_with_the_list_below_it(self):
        summary = self.report()["summary"]

        self.assertEqual(
            summary["status_breakdown"],
            {"paid": 0, "pending": 2, "overdue": 0, "waived": 1},
        )
        self.assertEqual(summary["payments"]["total_collections"], 400.0)
        self.assertEqual(summary["payments"]["cash"], 100.0)
        self.assertEqual(summary["payments"]["gcash"], 300.0)
        self.assertEqual(summary["payments"]["tx_count"], 2)
        self.assertEqual(summary["payment_submissions"]["total"]["count"], 1)
        self.assertEqual(summary["payment_submissions"]["approval_rate"], 100.0)

    def test_a_submission_covering_two_fees_shows_under_both_with_its_slice(self):
        other_fee = make_fee(self.student, self.tuition, amount="80.00")
        submit(
            self.student,
            [(self.fee, "50.00"), (other_fee, "80.00")],
            status=PENDING,
            reference="REF-2",
        )

        report = self.report()
        slices = {
            fee_id: [
                (s["reference_number"], s["amount_for_this_fee"], s["total_amount_paid"])
                for s in self.find_fee(report, fee_id)["submissions"]
                if s["reference_number"] == "REF-2"
            ]
            for fee_id in (self.fee.pk, other_fee.pk)
        }

        self.assertEqual(slices[self.fee.pk], [("REF-2", 50.0, 130.0)])
        self.assertEqual(slices[other_fee.pk], [("REF-2", 80.0, 130.0)])

    def test_the_nested_submission_carries_its_proof_and_context(self):
        submission = self.find_fee(self.report(), self.fee.pk)["submissions"][0]

        self.assertEqual(submission["previous_balance_for_this_fee"], 500.0)
        self.assertEqual(submission["screenshot_urls"], [])
        self.assertIsNotNone(submission["submitted_at"])
        self.assertIsNone(submission["remarks"])

    def test_the_flat_payments_section_lists_every_payment_with_totals(self):
        payments = self.report()["payments"]

        by_id = {p["id"]: p for p in payments["records"]}
        self.assertEqual(
            set(by_id), {self.linked_payment.pk, self.direct_payment.pk}
        )
        self.assertEqual(payments["totals"], {
            "count": 2,
            "amount_paid": 400.0,
            "active": {
                "count": 2, "amount_paid": 400.0, "cash": 100.0, "gcash": 300.0,
            },
            "cancelled": {"count": 0, "amount_paid": 0.0},
        })

        linked = by_id[self.linked_payment.pk]
        self.assertEqual(linked["source"], "submission")
        self.assertEqual(linked["submission_reference"], "REF-1")
        self.assertEqual(linked["payment_submission_id"], self.submission.pk)
        self.assertEqual(linked["previous_balance"], 500.0)
        direct = by_id[self.direct_payment.pk]
        self.assertEqual(direct["source"], "direct")
        self.assertIsNone(direct["payment_submission_id"])
        self.assertEqual(direct["previous_balance"], 200.0)

    def test_a_cancelled_payment_is_listed_flagged_and_split_out(self):
        # A 150 payment on the pending fee, reversed: the money went back on
        # the fee, but the row must stay visible with its own totals line.
        cancelled = pay(self.pending_fee, "150.00", by=self.cashier)
        Payment.objects.filter(pk=cancelled.pk).update(
            status=Payment.PaymentStatus.CANCELLED,
            cancelled_by=self.cashier,
            cancellation_reason="Encoding error",
        )
        self.pending_fee.balance = Decimal("200.00")
        self.pending_fee.save(update_fields=["balance"])

        payments = self.report()["payments"]

        record = next(
            p for p in payments["records"] if p["id"] == cancelled.pk
        )
        self.assertEqual(record["status"], "cancelled")
        self.assertEqual(record["cancelled_by"], self.cashier.username)
        self.assertEqual(record["cancellation_reason"], "Encoding error")
        self.assertEqual(payments["totals"], {
            "count": 3,
            "amount_paid": 550.0,
            "active": {
                "count": 2, "amount_paid": 400.0, "cash": 100.0, "gcash": 300.0,
            },
            "cancelled": {"count": 1, "amount_paid": 150.0},
        })

        # The nested tree and the summary stay active-only.
        nested = self.find_fee(self.report(), self.pending_fee.pk)
        self.assertEqual(nested["payments"], [])
        self.assertEqual(
            self.report()["summary"]["payments"]["total_collections"], 400.0
        )

    def test_the_losses_section_carries_the_waived_fee_and_its_history(self):
        # Waive after part-payment: 200 of 500 came in through an approved
        # submission before the remaining 300 was forgiven.
        submission = submit(
            self.student,
            [(self.waived_fee, "200.00")],
            reference="REF-WAIVED",
            reviewed_by=self.cashier,
        )
        pay(self.waived_fee, "200.00", by=self.cashier, submission=submission)
        Fee.objects.filter(pk=self.waived_fee.pk).update(
            status=Fee.FeeStatus.WAIVED, balance=Decimal("300.00")
        )

        losses = self.report()["losses"]

        self.assertEqual(losses["count"], 1)
        self.assertEqual(losses["billed"], 500.0)
        self.assertEqual(losses["collected_before_waive"], 200.0)
        self.assertEqual(losses["waived_amount"], 300.0)

        record = losses["records"][0]
        self.assertEqual(record["fee_id"], self.waived_fee.pk)
        self.assertEqual(record["paid_before_waive"], 200.0)
        self.assertEqual(record["waived_amount"], 300.0)
        self.assertEqual(record["remarks"], "Working scholar")
        self.assertEqual(
            [(p["amount_paid"], p["status"], p["submission_reference"])
             for p in record["payments"]],
            [(200.0, "active", "REF-WAIVED")],
        )
        self.assertEqual(
            [(i["amount_paid"], i["submission_reference"])
             for i in record["submission_items"]],
            [(200.0, "REF-WAIVED")],
        )

    def test_an_untouched_waived_fee_is_a_whole_bill_lost(self):
        losses = self.report()["losses"]

        self.assertEqual(losses["count"], 1)
        self.assertEqual(losses["waived_amount"], 500.0)
        self.assertEqual(losses["collected_before_waive"], 0.0)
        record = losses["records"][0]
        self.assertEqual(record["payments"], [])
        self.assertEqual(record["submission_items"], [])

    def test_the_submissions_section_lists_each_submission_exactly_once(self):
        # Covers two fees - one record all the same, with both items counted.
        other_fee = make_fee(self.student, self.tuition, amount="80.00")
        submit(
            self.student,
            [(self.fee, "50.00"), (other_fee, "80.00")],
            status=PENDING,
            reference="REF-2",
        )

        section = self.report()["payment_submissions"]
        self.assertEqual(
            sorted(s["reference_number"] for s in section["records"]),
            ["REF-1", "REF-2"],
        )
        self.assertEqual(
            section["totals"], {"count": 2, "total_amount_paid": 430.0}
        )

        ref2 = next(
            s for s in section["records"] if s["reference_number"] == "REF-2"
        )
        self.assertEqual(ref2["fee_count"], 2)
        self.assertEqual(ref2["status"], PENDING)
        self.assertEqual(ref2["student_number"], self.student.s_studentID)
        self.assertEqual(ref2["screenshot_urls"], [])
        self.assertIsNone(ref2["reviewed_by"])
        self.assertIsNotNone(ref2["submitted_at"])

    def test_the_items_section_carries_each_fees_slice(self):
        items = self.report()["payment_submission_items"]

        self.assertEqual(items["totals"], {"count": 1, "amount_paid": 300.0})
        record = items["records"][0]
        self.assertEqual(record["payment_submission_id"], self.submission.pk)
        self.assertEqual(record["submission_reference"], "REF-1")
        self.assertEqual(record["submission_status"], APPROVED)
        self.assertEqual(record["fee_id"], self.fee.pk)
        self.assertEqual(record["previous_balance"], 500.0)
        self.assertEqual(record["amount_paid"], 300.0)

    def test_a_submission_linked_only_through_a_payment_still_appears(self):
        # An approved submission whose item row never got written - the
        # payment is its only link to the fee. It must not vanish.
        orphan = PaymentSubmission.objects.create(
            student=self.student,
            screenshot=[],
            total_amount_paid=Decimal("40.00"),
            reference_number="REF-ORPHAN",
            status=APPROVED,
        )
        pay(self.pending_fee, "40.00", by=self.cashier, submission=orphan)

        report = self.report()
        self.assertIn(
            "REF-ORPHAN",
            [s["reference_number"] for s in report["payment_submissions"]["records"]],
        )
        orphan_payment = next(
            p for p in report["payments"]["records"]
            if p["submission_reference"] == "REF-ORPHAN"
        )
        self.assertEqual(orphan_payment["source"], "submission")
        self.assertEqual(orphan_payment["fee_id"], self.pending_fee.pk)

    def test_screenshot_public_ids_become_links(self):
        PaymentSubmission.objects.filter(pk=self.submission.pk).update(
            screenshot=["payment_submissions/abc123"]
        )

        record = next(
            s for s in self.report()["payment_submissions"]["records"]
            if s["id"] == self.submission.pk
        )
        self.assertEqual(len(record["screenshot_urls"]), 1)
        self.assertIn("abc123", record["screenshot_urls"][0])


class StaffReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.ramos = make_staff(cls.institute)
        cls.cruz = make_staff(cls.institute)
        category = make_category(cls.institute, name="Tuition Fee")

        student = make_student(cls.program)
        fee = make_fee(student, category, amount="1000.00")

        submission = submit(student, [(fee, "300.00")])
        pay(fee, "300.00", method=Payment.PaymentMethod.GCASH, by=cls.ramos,
            submission=submission)
        pay(fee, "100.00", by=cls.ramos)
        pay(fee, "100.00", by=cls.cruz)
        # No receiver recorded - belongs to nobody's tally.
        pay(fee, "50.00", by=None)

    def report(self):
        return build_staff_report(user=self.staff, filters=term_filters())

    def test_staff_are_sorted_by_collected_descending(self):
        staff = self.report()["staff"]

        self.assertEqual(
            [member["staff_username"] for member in staff],
            [self.ramos.username, self.cruz.username],
        )

    def test_each_member_carries_their_own_totals(self):
        ramos = self.report()["staff"][0]["totals"]

        self.assertEqual(ramos["collected"], 400.0)
        self.assertEqual(ramos["tx_count"], 2)
        self.assertEqual(ramos["cash"], 100.0)
        self.assertEqual(ramos["gcash"], 300.0)
        self.assertEqual(ramos["share_of_total"], 80.0)

    def test_shares_add_up_to_one_hundred(self):
        staff = self.report()["staff"]

        self.assertAlmostEqual(
            sum(member["totals"]["share_of_total"] for member in staff), 100.0
        )

    def test_unattributed_payments_are_left_out(self):
        report = self.report()

        # The 50 with no receiver is in nobody's rows and nobody's total.
        self.assertEqual(
            report["totals"],
            {"collected": 500.0, "tx_count": 3, "cash": 200.0, "gcash": 300.0},
        )

    def test_a_payment_row_names_its_submission_when_it_has_one(self):
        payments = self.report()["staff"][0]["payments"]
        references = {p["id"]: p["submission_reference"] for p in payments}

        self.assertIn("REF-1", references.values())
        self.assertIn(None, references.values())


class ProgramSetReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.staff = make_staff(cls.institute)
        cls.tuition = make_category(cls.institute, name="Tuition Fee")
        cls.athletics = make_category(cls.institute, name="Athletics Fee")

        cls.cs = make_program(cls.institute)
        cls.cs.name = "BS Computer Science"
        cls.cs.save(update_fields=["name"])
        cls.it = make_program(cls.institute)
        cls.it.name = "BS Information Technology"
        cls.it.save(update_fields=["name"])

        def enrol(program, level, s_set, last_name):
            student = make_student(program, level=level)
            student.s_set = s_set
            student.s_lname = last_name
            student.save(update_fields=["s_set", "s_lname"])
            return student

        # CS year 1: two sets, one of them blank; year 2: one set.
        cls.abad = enrol(cls.cs, 1, "A", "Abad")
        cls.belen = enrol(cls.cs, 1, "A", "Belen")
        cls.cruz = enrol(cls.cs, 1, None, "Cruz")
        cls.diaz = enrol(cls.cs, 2, "A", "Diaz")
        cls.enano = enrol(cls.it, 1, "B", "Enano")

        # Abad: two fees in different categories, partly paid.
        fee = make_fee(cls.abad, cls.tuition, amount="500.00")
        pay(fee, "200.00", by=cls.staff)
        make_fee(cls.abad, cls.athletics, amount="100.00")
        # Belen: fully paid; plus a waived fee that must not move any total.
        paid = make_fee(cls.belen, cls.tuition, amount="300.00")
        pay(paid, "300.00", by=cls.staff)
        make_fee(cls.belen, cls.athletics, amount="400.00", status=Fee.FeeStatus.WAIVED)
        # The rest: one plain pending fee each.
        make_fee(cls.cruz, cls.tuition, amount="100.00")
        make_fee(cls.diaz, cls.tuition, amount="100.00")
        make_fee(cls.enano, cls.tuition, amount="100.00")

    def report(self):
        return build_program_set_report(user=self.staff, filters=term_filters())

    def test_the_nesting_is_ordered_for_reading(self):
        report = self.report()

        self.assertEqual(
            [p["program"] for p in report["programs"]],
            ["BS Computer Science", "BS Information Technology"],
        )
        cs = report["programs"][0]
        self.assertEqual([l["year_level"] for l in cs["year_levels"]], [1, 2])
        # Named sets first, the blank one ("—") last.
        self.assertEqual(
            [s["set"] for s in cs["year_levels"][0]["sets"]], ["A", "—"]
        )
        self.assertEqual(
            [s["student_name"] for s in cs["year_levels"][0]["sets"][0]["students"]],
            [f"Test {self.abad.s_lname}", f"Test {self.belen.s_lname}"],
        )

    def test_a_students_fees_are_sorted_by_category(self):
        abad = self.report()["programs"][0]["year_levels"][0]["sets"][0]["students"][0]

        self.assertEqual(
            [fee["category"] for fee in abad["fees"]],
            ["Athletics Fee", "Tuition Fee"],
        )

    def test_every_level_sums_exactly_to_its_parent(self):
        report = self.report()

        for key in ("billed", "paid", "balance", "student_count"):
            self.assertAlmostEqual(
                sum(p["totals"][key] for p in report["programs"]),
                report["totals"][key],
                msg=f"programs do not sum to the grand total on {key}",
            )
            for program in report["programs"]:
                self.assertAlmostEqual(
                    sum(l["totals"][key] for l in program["year_levels"]),
                    program["totals"][key],
                    msg=f"{program['program']} year levels do not sum on {key}",
                )
                for level in program["year_levels"]:
                    self.assertAlmostEqual(
                        sum(s["totals"][key] for s in level["sets"]),
                        level["totals"][key],
                        msg=f"sets do not sum on {key}",
                    )

        for program in report["programs"]:
            for level in program["year_levels"]:
                for group in level["sets"]:
                    for money_key in ("billed", "paid", "balance"):
                        self.assertAlmostEqual(
                            sum(s["totals"][money_key] for s in group["students"]),
                            group["totals"][money_key],
                            msg=f"students do not sum on {money_key}",
                        )

    def test_the_waived_fee_is_listed_but_moves_no_total(self):
        belen = self.report()["programs"][0]["year_levels"][0]["sets"][0]["students"][1]

        self.assertIn(
            Fee.FeeStatus.WAIVED, [fee["status"] for fee in belen["fees"]]
        )
        # 300 billed and fully paid; the waived 400 is nowhere in the money.
        self.assertEqual(belen["totals"], {
            "billed": 300.0, "discounts": 0.0, "expected": 300.0,
            "paid": 300.0, "balance": 0.0, "collection_rate": 100.0,
        })

    def test_the_grand_total_counts_every_student_once(self):
        report = self.report()

        self.assertEqual(report["totals"]["student_count"], 5)
        self.assertEqual(report["totals"]["billed"], 1200.0)
        self.assertEqual(report["totals"]["paid"], 500.0)


class DetailedViewTests(TestCase):
    """The three endpoints share permissions and the mandatory-period rule."""

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.staff = make_staff(cls.institute)
        program = make_program(cls.institute)
        category = make_category(cls.institute, name="Tuition Fee")
        student = make_student(program)
        fee = make_fee(student, category, amount="100.00")
        # A submission-born payment and a counter payment, plus a screenshot,
        # so the PDF and workbook render every section with real rows.
        submission = submit(
            student, [(fee, "40.00")], reviewed_by=cls.staff,
        )
        PaymentSubmission.objects.filter(pk=submission.pk).update(
            screenshot=["payment_submissions/proof1"]
        )
        pay(fee, "40.00", method=Payment.PaymentMethod.GCASH, by=cls.staff,
            submission=submission)
        pay(fee, "60.00", by=cls.staff)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    PATHS = ["detailed-fees", "by-staff", "by-program-set"]

    def test_the_urls_keep_their_trailing_slash(self):
        for name in self.PATHS:
            self.assertEqual(reverse(name), f"/api/v1/summaries/{name}/")

    def test_a_request_with_no_period_is_refused(self):
        for name in self.PATHS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 400)

    def test_half_a_period_is_still_no_period(self):
        for query in (
            {"academic_year": ACADEMIC_YEAR},  # no semester
            {"semester": SEMESTER},  # no year
            {"date_from": "2026-01-01"},  # no end
            {"date_to": "2026-06-30"},  # no start
        ):
            response = self.client.get(reverse("detailed-fees"), query)
            self.assertEqual(response.status_code, 400, query)

    def test_a_term_or_a_full_date_range_is_accepted(self):
        for name in self.PATHS:
            url = reverse(name)
            with_term = self.client.get(
                url, {"academic_year": ACADEMIC_YEAR, "semester": SEMESTER}
            )
            with_range = self.client.get(
                url, {"date_from": "2026-01-01", "date_to": "2026-12-31"}
            )
            self.assertEqual(with_term.status_code, 200, name)
            self.assertEqual(with_range.status_code, 200, name)

    def test_students_and_anonymous_are_refused(self):
        student_user = User.objects.create_user(
            username="detailed-student", password="pw", institute=self.institute
        )
        for name in self.PATHS:
            url = reverse(name)
            self.client.force_authenticate(student_user)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.client.force_authenticate(None)
            self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_authenticate(self.staff)

    def test_every_pdf_endpoint_returns_a_pdf(self):
        for name in self.PATHS:
            response = self.client.get(
                reverse(f"{name}-pdf"),
                {"academic_year": ACADEMIC_YEAR, "semester": SEMESTER},
            )
            self.assertEqual(response.status_code, 200, name)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(response.content.startswith(b"%PDF"), name)

    def test_every_excel_endpoint_returns_a_workbook(self):
        for name in self.PATHS:
            response = self.client.get(
                reverse(f"{name}-excel"),
                {"academic_year": ACADEMIC_YEAR, "semester": SEMESTER},
            )
            self.assertEqual(response.status_code, 200, name)
            self.assertIn("spreadsheetml", response["Content-Type"])
            self.assertIn(".xlsx", response["Content-Disposition"])
            # An .xlsx file is a zip archive.
            self.assertTrue(response.content.startswith(b"PK\x03\x04"), name)

    def test_the_excel_endpoints_also_require_a_period(self):
        for name in self.PATHS:
            response = self.client.get(reverse(f"{name}-excel"))
            self.assertEqual(response.status_code, 400, name)

    def test_the_detailed_fees_workbook_tabs_come_in_reading_order(self):
        from io import BytesIO

        from openpyxl import load_workbook

        response = self.client.get(
            reverse("detailed-fees-excel"),
            {"academic_year": ACADEMIC_YEAR, "semester": SEMESTER},
        )

        workbook = load_workbook(BytesIO(response.content), read_only=True)
        self.assertEqual(
            workbook.sheetnames,
            ["Summary", "Fees", "Payments", "Payment Submissions",
             "Payment Submission Items", "Losses"],
        )
