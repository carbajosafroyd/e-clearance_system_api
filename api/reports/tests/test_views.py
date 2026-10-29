from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

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
from api.users.models import User


class CollectionReportViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.category = make_category(cls.institute, name="Tuition Fee")

        student = make_student(cls.program)
        fee = make_fee(student, cls.category, amount="100.00")
        Payment.objects.create(
            fee=fee,
            amount_paid=Decimal("100.00"),
            previous_balance=fee.balance,
            received_by=cls.staff,
        )

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("collection-report")
        self.pdf_url = reverse("collection-report-pdf")

    def payload(self, response):
        """The report itself, out of the project's response envelope."""
        return response.json()["data"]

    def test_staff_get_the_whole_report_in_one_request(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(
            self.url, {"academic_year": ACADEMIC_YEAR, "semester": SEMESTER}
        )

        self.assertEqual(response.status_code, 200)
        data = self.payload(response)
        self.assertEqual(data["kpi"]["total_collections"], 100.0)
        for section in (
            "generated_at",
            "kpi",
            "status_breakdown",
            "categories",
            "staff",
            "trend",
            "payment_methods",
            "program_sets",
            "filter_options",
        ):
            self.assertIn(section, data)

    def test_the_url_keeps_its_trailing_slash(self):
        self.assertEqual(self.url, "/api/v1/summaries/collection-report/")

    def test_a_non_staff_account_is_refused(self):
        student_user = User.objects.create_user(
            username="a-student", password="pw", institute=self.institute
        )
        self.client.force_authenticate(student_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_a_backwards_date_range_is_rejected(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(
            self.url, {"date_from": "2026-06-30", "date_to": "2026-01-01"}
        )

        self.assertEqual(response.status_code, 400)

    def test_an_unknown_semester_is_rejected(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(self.url, {"semester": "3rd"})

        self.assertEqual(response.status_code, 400)

    def test_no_filters_at_all_is_allowed(self):
        self.client.force_authenticate(self.staff)

        self.assertEqual(self.client.get(self.url).status_code, 200)


class CollectionReportPdfViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        category = make_category(cls.institute, name="Tuition Fee")
        student = make_student(cls.program)
        make_fee(student, category, amount="100.00")

    def setUp(self):
        self.client = APIClient()
        self.url = reverse("collection-report-pdf")

    def test_it_returns_a_pdf_attachment(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(
            self.url, {"academic_year": ACADEMIC_YEAR, "semester": SEMESTER}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_an_empty_report_still_renders(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(self.url, {"academic_year": "2022-2023"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_the_excel_endpoint_returns_a_workbook(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(
            reverse("collection-report-excel"),
            {"academic_year": ACADEMIC_YEAR, "semester": SEMESTER},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        self.assertIn(".xlsx", response["Content-Disposition"])
        # An .xlsx file is a zip archive.
        self.assertTrue(response.content.startswith(b"PK\x03\x04"))

    def test_a_non_staff_account_is_refused(self):
        student_user = User.objects.create_user(
            username="pdf-student", password="pw", institute=self.institute
        )
        self.client.force_authenticate(student_user)

        self.assertEqual(self.client.get(self.url).status_code, 403)
