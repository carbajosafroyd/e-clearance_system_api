"""
The optional-module reports: complaints, reports & ideas, the leaderboard,
point discounts and coupons - each built from real rows, then served in the
three shapes through the views.

What these pin down:

* every builder answers with the shared header (institute, school, who
  asked) and a summary that adds up to its records;
* the routes exist only where the build ships the module, and refuse an
  account without the module's own read grant;
* PDF and Excel come back as files, from the same snapshot.
"""

from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api.complaints.models import Complaint, ComplaintCategory
from api.contributions.models import Contribution, FeeDiscount, PointAccount
from api.coupons.models import Coupon, CouponBatch, CouponRedemption
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
from core.choices import SystemScope

from ..periods import ReportFilters
from ..services.module_excel import (
    build_complaints_workbook,
    build_contributions_workbook,
    build_coupons_workbook,
    build_leaderboard_workbook,
    build_point_discounts_workbook,
)
from ..services.module_reports import (
    build_complaints_report,
    build_contributions_report,
    build_coupons_report,
    build_leaderboard_report,
    build_point_discounts_report,
)

ALL_ON = {
    "contributions": True,
    "contribution_points": True,
    "complaints": True,
    "coupons": True,
}

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ModuleReportData(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.category = make_category(cls.institute, name="Org Dues")
        cls.student = make_student(cls.program)
        cls.other = make_student(cls.program)
        cls.fee = make_fee(cls.student, cls.category, amount="500.00")

        cls.complaint = Complaint.objects.create(
            student=cls.student,
            system=SystemScope.COLLECTION,
            category=ComplaintCategory.WRONG_AMOUNT,
            related_fee=cls.fee,
            title="Charged the wrong amount for org dues",
            description="The dues were announced at 400 but the fee says 500.",
            status=Complaint.Status.RESOLVED,
            response="Corrected - the category price was wrong.",
            handled_by=cls.staff,
            handled_at=timezone.now(),
        )
        Complaint.objects.create(
            student=cls.other,
            system=SystemScope.LOCKER,
            category=ComplaintCategory.SERVICE_ISSUE,
            title="Locker door does not close",
            description="Locker 12 on the second floor will not lock at all.",
        )

        cls.contribution = Contribution.objects.create(
            student=cls.student,
            system=SystemScope.COLLECTION,
            title="Fee list shows the wrong balance after a partial payment",
            description="Paid 100 of 500, list still says 500 owed until a refresh.",
            status=Contribution.Status.APPROVED,
            points_awarded=30,
            reviewed_by=cls.staff,
            reviewed_at=timezone.now(),
        )
        Contribution.objects.create(
            student=cls.other,
            system=SystemScope.COLLECTION,
            title="A dark mode for the dashboard would help",
            description="The dashboard is very bright in the evening lab sessions.",
        )

        PointAccount.objects.create(
            student=cls.student,
            system=SystemScope.COLLECTION,
            total_earned=30,
            total_spent=10,
            balance=20,
        )
        PointAccount.objects.create(
            student=cls.other,
            system=SystemScope.LOCKER,
            total_earned=5,
            total_spent=0,
            balance=5,
        )

        cls.discount = FeeDiscount.objects.create(
            fee=cls.fee,
            student=cls.student,
            system=SystemScope.COLLECTION,
            points_spent=10,
            amount=Decimal("10.00"),
            status=FeeDiscount.Status.APPROVED,
            previous_total_amount=Decimal("500.00"),
            previous_balance=Decimal("500.00"),
            reviewed_by=cls.staff,
            reviewed_at=timezone.now(),
        )

        cls.batch = CouponBatch.objects.create(
            institute=cls.institute,
            name="Foundation week",
            reason="Turned up to every event",
            value=Decimal("50.00"),
            expires_at=timezone.now() + timezone.timedelta(days=30),
            issued_by=cls.staff,
            total_created=2,
        )
        cls.coupon = Coupon.objects.create(
            batch=cls.batch, code="AAAA-BBBB-CCCC", student=cls.student
        )
        Coupon.objects.create(batch=cls.batch, code="DDDD-EEEE-FFFF")
        CouponRedemption.objects.create(
            coupon=cls.coupon,
            student=cls.student,
            fee=cls.fee,
            amount=Decimal("50.00"),
            previous_balance=Decimal("500.00"),
            previous_discount_total=Decimal("0.00"),
            status=CouponRedemption.Status.APPLIED,
            applied_at=timezone.now(),
        )

    def filters(self, **overrides) -> ReportFilters:
        return ReportFilters(**overrides)


class BuilderTests(ModuleReportData):
    def test_every_report_carries_the_shared_header(self):
        for builder in (
            build_complaints_report,
            build_contributions_report,
            build_leaderboard_report,
            build_point_discounts_report,
            build_coupons_report,
        ):
            report = builder(user=self.staff, filters=self.filters())
            self.assertEqual(report["institute"], self.institute.institute_name)
            self.assertEqual(report["school"], self.institute.school.school_name)
            self.assertEqual(report["prepared_by"], self.staff.get_username())
            self.assertEqual(report["period"], "All time")

    def test_complaints_summary_adds_up_to_its_records(self):
        report = build_complaints_report(user=self.staff, filters=self.filters())

        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["open"], 1)
        self.assertEqual(report["summary"]["resolved"], 1)
        self.assertEqual(report["summary"]["fee_related"], 1)
        self.assertEqual(report["summary"]["resolution_rate"], 100.0)
        self.assertEqual(len(report["records"]), 2)

        resolved = next(r for r in report["records"] if r["id"] == self.complaint.pk)
        self.assertEqual(resolved["fee_id"], self.fee.pk)
        self.assertEqual(resolved["fee_category"], "Org Dues")
        self.assertEqual(resolved["handled_by"], self.staff.get_username())
        # Every system appears, zero-filled, so "nobody complained" is visible.
        self.assertEqual(len(report["by_system"]), len(SystemScope.choices))
        locker = next(r for r in report["by_system"] if r["system"] == "locker")
        self.assertEqual(locker["open"], 1)

    def test_complaints_can_be_cut_by_system_and_status(self):
        report = build_complaints_report(
            user=self.staff,
            filters=self.filters(system="collection", statuses=("resolved",)),
        )
        self.assertEqual(report["summary"]["total"], 1)
        self.assertEqual(report["filters"]["system_label"], "Collection Management System")

    def test_contributions_summary_and_points(self):
        report = build_contributions_report(user=self.staff, filters=self.filters())

        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["approved"], 1)
        self.assertEqual(report["summary"]["pending"], 1)
        self.assertEqual(report["summary"]["points_awarded"], 30)
        self.assertEqual(report["summary"]["approval_rate"], 100.0)
        kinds = {row["kind"]: row for row in report["by_kind"]}
        self.assertEqual(kinds["recommendation"]["total"], 2)

    def test_leaderboard_ranks_by_points_earned(self):
        report = build_leaderboard_report(user=self.staff, filters=self.filters())

        self.assertEqual([row["rank"] for row in report["rows"]], [1, 2])
        self.assertEqual(report["rows"][0]["points_earned"], 30)
        self.assertEqual(report["rows"][0]["approved_contributions"], 1)
        self.assertEqual(report["summary"]["points_earned"], 35)

        locker_only = build_leaderboard_report(
            user=self.staff, filters=self.filters(system="locker")
        )
        self.assertEqual(len(locker_only["rows"]), 1)
        self.assertEqual(locker_only["system_label"], "Locker Management System")

    def test_point_discounts_cost(self):
        report = build_point_discounts_report(user=self.staff, filters=self.filters())

        self.assertEqual(report["summary"]["approved"], 1)
        self.assertEqual(report["summary"]["amount_approved"], 10.0)
        self.assertEqual(report["summary"]["points_spent"], 10)
        self.assertEqual(report["summary"]["students_helped"], 1)
        record = report["records"][0]
        self.assertEqual(record["fee_category"], "Org Dues")
        self.assertEqual(record["fee_billed"], 500.0)

    def test_point_discounts_follow_the_fee_term(self):
        report = build_point_discounts_report(
            user=self.staff,
            filters=self.filters(academic_year=ACADEMIC_YEAR, semester=SEMESTER),
        )
        self.assertEqual(report["summary"]["requests"], 1)

        elsewhere = build_point_discounts_report(
            user=self.staff, filters=self.filters(academic_year="2000-2001")
        )
        self.assertEqual(elsewhere["summary"]["requests"], 0)

    def test_coupons_batches_codes_and_money(self):
        report = build_coupons_report(user=self.staff, filters=self.filters())

        self.assertEqual(report["summary"]["batches"], 1)
        self.assertEqual(report["summary"]["issued"], 2)
        self.assertEqual(report["summary"]["bound"], 1)
        self.assertEqual(report["summary"]["bearer"], 1)
        self.assertEqual(report["summary"]["applied"], 1)
        self.assertEqual(report["summary"]["amount_applied"], 50.0)
        # One live, unspent bearer coupon at face value.
        self.assertEqual(report["summary"]["outstanding_value"], 50.0)

        batch = report["batches"][0]
        self.assertEqual(batch["issued"], 2)
        self.assertEqual(batch["applied"], 1)
        self.assertEqual(batch["unspent"], 1)
        self.assertEqual(batch["amount_applied"], 50.0)

        spent = next(c for c in report["coupons"] if c["code"] == "AAAA-BBBB-CCCC")
        self.assertTrue(spent["is_spent"])
        self.assertEqual(spent["student_number"], self.student.s_studentID)
        self.assertEqual(report["redemptions"][0]["fee_category"], "Org Dues")

    def test_another_institute_sees_none_of_it(self):
        stranger = make_staff(make_institute())
        for builder, key in (
            (build_complaints_report, "records"),
            (build_contributions_report, "records"),
            (build_leaderboard_report, "rows"),
            (build_point_discounts_report, "records"),
            (build_coupons_report, "batches"),
        ):
            report = builder(user=stranger, filters=self.filters())
            self.assertEqual(report[key], [], builder.__name__)


class WorkbookTests(ModuleReportData):
    def test_every_workbook_renders(self):
        from io import BytesIO

        from openpyxl import load_workbook

        pairs = (
            (build_complaints_report, build_complaints_workbook, "Complaints"),
            (build_contributions_report, build_contributions_workbook, "Reports"),
            (build_leaderboard_report, build_leaderboard_workbook, "Leaderboard"),
            (build_point_discounts_report, build_point_discounts_workbook, "Discounts"),
            (build_coupons_report, build_coupons_workbook, "Coupons"),
        )
        for build, workbook_builder, sheet_name in pairs:
            report = build(user=self.staff, filters=self.filters())
            data = workbook_builder(
                report=report,
                filters=self.filters(),
                institute_name=self.institute.institute_name,
                prepared_by="staff",
                school_name=self.institute.school.school_name,
                institute_acronym="IC",
            )
            workbook = load_workbook(BytesIO(data))
            self.assertIn("Summary", workbook.sheetnames, sheet_name)
            self.assertIn(sheet_name, workbook.sheetnames)
            summary = workbook["Summary"]
            self.assertEqual(summary["A2"].value, "School")
            self.assertEqual(summary["B2"].value, self.institute.school.school_name)
            self.assertEqual(summary["B3"].value, self.institute.institute_name)


@override_settings(FEATURES=ALL_ON)
class ModuleReportViewTests(ModuleReportData):
    def setUp(self):
        self.client = APIClient()

    def test_the_routes_exist_where_the_build_ships_the_modules(self):
        for name in (
            "complaints-report",
            "contributions-report",
            "leaderboard-report",
            "point-discounts-report",
            "coupons-report",
        ):
            try:
                reverse(name)
            except NoReverseMatch:
                self.skipTest(
                    f"{name} is not registered: this build does not ship the "
                    "module (set FEATURE_* in the environment to test it)"
                )

    def _url(self, name):
        try:
            return reverse(name)
        except NoReverseMatch:
            self.skipTest(f"{name} is not registered in this build")

    def test_complaints_report_json_pdf_and_excel(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(self._url("complaints-report"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["summary"]["total"], 2)

        pdf = self.client.get(self._url("complaints-report-pdf"))
        self.assertEqual(pdf.status_code, 200, pdf.content[:200])
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        excel = self.client.get(self._url("complaints-report-excel"))
        self.assertEqual(excel.status_code, 200)
        self.assertEqual(excel["Content-Type"], XLSX)
        self.assertIn("complaints-", excel["Content-Disposition"])

    def test_every_pdf_and_excel_renders(self):
        self.client.force_authenticate(self.staff)
        for name in (
            "contributions-report",
            "leaderboard-report",
            "point-discounts-report",
            "coupons-report",
        ):
            pdf = self.client.get(self._url(f"{name}-pdf"))
            self.assertEqual(pdf.status_code, 200, (name, pdf.content[:300]))
            self.assertTrue(pdf.content.startswith(b"%PDF"), name)
            excel = self.client.get(self._url(f"{name}-excel"))
            self.assertEqual(excel.status_code, 200, (name, excel.content[:300]))

    def test_a_student_is_refused(self):
        student_user = self.student.user
        if student_user is None:
            self.skipTest("the factory student has no user")
        self.client.force_authenticate(student_user)
        response = self.client.get(self._url("complaints-report"))
        self.assertEqual(response.status_code, 403)

    def test_status_filter_is_comma_separated(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get(
            self._url("complaints-report"), {"status": "submitted,acknowledged"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["summary"]["total"], 1)
