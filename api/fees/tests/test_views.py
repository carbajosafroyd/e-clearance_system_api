from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.choices import Semester

from ..models import Fee, GeneratedFeeBatch
from .factories import (
    ACADEMIC_YEAR,
    SEMESTER,
    make_attendance_category,
    make_category,
    make_event,
    make_fee,
    make_institute,
    make_program,
    make_staff,
    make_student,
)


class FeeCreateEndpointTests(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        self.student = make_student(self.program)
        self.admin = make_staff(self.institute, superuser=True)
        self.client.force_authenticate(user=self.admin)

    def test_creating_a_fee_derives_the_balance(self):
        """Regression: perform_create called `serializer.get("total_amount")`,
        which serializers do not implement - every POST 500'd."""
        response = self.client.post(
            reverse("fees-list"),
            {
                "student": self.student.pk,
                "category": self.category.pk,
                "total_amount": "450.00",
                "due_date": (timezone.now() + timedelta(days=10)).isoformat(),
                "academic_year": ACADEMIC_YEAR,
                "semester": SEMESTER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        fee = Fee.objects.get()
        self.assertEqual(fee.total_amount, Decimal("450.00"))
        self.assertEqual(fee.balance, Decimal("450.00"))
        self.assertEqual(fee.issued_by, self.admin)

    def test_negative_amounts_are_rejected(self):
        response = self.client.post(
            reverse("fees-list"),
            {
                "student": self.student.pk,
                "category": self.category.pk,
                "total_amount": "-10.00",
                "due_date": (timezone.now() + timedelta(days=10)).isoformat(),
                "academic_year": ACADEMIC_YEAR,
                "semester": SEMESTER,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class FeeListEndpointTests(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        self.student = make_student(self.program)
        self.admin = make_staff(self.institute, superuser=True)
        self.client.force_authenticate(user=self.admin)

    def test_lists_fees_whose_issuer_was_deleted(self):
        """Regression: issued_by is SET_NULL, and the slim serializer walked
        `issued_by.username` unguarded."""
        make_fee(self.student, self.category)  # issued_by is null

        response = self.client.get(reverse("fees-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_staff_cannot_read_another_institutes_fee(self):
        other_program = make_program(make_institute())
        foreign_fee = make_fee(
            make_student(other_program), make_category(other_program.institute)
        )
        self.client.force_authenticate(user=make_staff(self.institute))

        response = self.client.get(reverse("fees-detail", args=[foreign_fee.pk]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AttendanceActionTests(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.student = make_student(self.program)
        self.attendance_category = make_attendance_category(self.institute)
        self.admin = make_staff(self.institute, superuser=True)
        self.client.force_authenticate(user=self.admin)

    def test_unknown_fee_is_a_404_not_a_500(self):
        """Regression: this action used `.get(pk=pk)` directly, so a bad id
        raised Fee.DoesNotExist instead of returning 404."""
        response = self.client.get(reverse("fees-attendance-settings", args=[999999]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_attendance_fee_is_rejected(self):
        fee = make_fee(self.student, make_category(self.institute, name="Uniform"))

        response = self.client.get(
            reverse("fees-attendance-settings", args=[fee.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_attendance_fee_without_an_event_returns_404(self):
        fee = make_fee(self.student, self.attendance_category)

        response = self.client.get(
            reverse("fees-attendance-settings", args=[fee.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_returns_the_event_configuration(self):
        event = make_event(self.institute, days=1)
        fee = make_fee(
            self.student,
            self.attendance_category,
            institute_attendance_event=event,
        )

        response = self.client.get(
            reverse("fees-attendance-settings", args=[fee.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["event"]["id"], event.pk)

    def test_details_report_no_records(self):
        fee = make_fee(self.student, self.attendance_category)

        response = self.client.get(reverse("fees-attendance-details", args=[fee.pk]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["has_record"])


class OverdueEndpointTests(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        self.student = make_student(self.program)
        self.admin = make_staff(self.institute, superuser=True)
        self.client.force_authenticate(user=self.admin)

    def test_reports_what_it_changed(self):
        fee = make_fee(
            self.student,
            self.category,
            due_date=timezone.now() - timedelta(days=2),
        )

        response = self.client.post(reverse("fees-update-overdue-fees"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["updated_count"], 1)
        self.assertEqual(response.data["fee_ids"], [fee.pk])


class BatchDeletionTests(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        self.student = make_student(self.program)
        self.admin = make_staff(self.institute, superuser=True)
        self.client.force_authenticate(user=self.admin)

        self.batch = GeneratedFeeBatch.objects.create(
            category=self.category,
            institute=self.institute,
            academic_year=ACADEMIC_YEAR,
            semester=SEMESTER,
            generated_by=self.admin,
        )

    def test_deleting_a_batch_removes_its_fees(self):
        make_fee(self.student, self.category, gen_fee_batch=self.batch)

        response = self.client.delete(
            reverse("generate-fees-detail", args=[self.batch.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Fee.objects.count(), 0)

    def test_refuses_to_delete_a_batch_with_paid_fees(self):
        make_fee(
            self.student,
            self.category,
            gen_fee_batch=self.batch,
            status=Fee.FeeStatus.PAID,
        )

        response = self.client.delete(
            reverse("generate-fees-detail", args=[self.batch.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Fee.objects.count(), 1)

    def test_staff_cannot_see_another_institutes_batches(self):
        self.client.force_authenticate(user=make_staff(make_institute()))

        response = self.client.get(reverse("generate-fees-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_items"], 0)


class BatchListFilterTests(APITestCase):
    """The /generate-fees/ list ignored every query param until the filterset
    landed; these pin down the params the frontend's filter modal sends."""

    def setUp(self):
        # The list is cached and invalidation rides on_commit, which never
        # fires inside a test transaction - start each test cold (same
        # convention as the audits tests).
        cache.clear()

        self.institute = make_institute()
        self.admin = make_staff(self.institute, superuser=True)
        self.other_staff = make_staff(self.institute)
        self.client.force_authenticate(user=self.admin)

        self.library = make_category(self.institute, name="Library Fee")
        self.uniform = make_category(self.institute, name="Uniform Fee")

        self.library_batch = GeneratedFeeBatch.objects.create(
            category=self.library,
            institute=self.institute,
            academic_year=ACADEMIC_YEAR,
            semester=Semester.FIRST,
            generated_by=self.admin,
            total_created_fees=120,
        )
        self.uniform_batch = GeneratedFeeBatch.objects.create(
            category=self.uniform,
            institute=self.institute,
            academic_year=ACADEMIC_YEAR,
            semester=Semester.SECOND,
            generated_by=self.other_staff,
            total_created_fees=5,
        )
        self.url = reverse("generate-fees-list")

    def _ids(self, params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {row["id"] for row in response.data["data"]}

    def test_filter_by_category_name(self):
        # `category` is the free-text input the deployed filter modal sends.
        self.assertEqual(
            self._ids({"category": "library"}), {self.library_batch.pk}
        )

    def test_filter_by_category_id(self):
        self.assertEqual(
            self._ids({"category_id": self.uniform.pk}), {self.uniform_batch.pk}
        )

    def test_filter_by_semester(self):
        self.assertEqual(
            self._ids({"semester": Semester.FIRST}), {self.library_batch.pk}
        )

    def test_filter_by_semester_in(self):
        self.assertEqual(
            self._ids({"semester__in": "1st,2nd"}),
            {self.library_batch.pk, self.uniform_batch.pk},
        )

    def test_filter_by_academic_year(self):
        self.assertEqual(
            self._ids({"academic_year": ACADEMIC_YEAR}),
            {self.library_batch.pk, self.uniform_batch.pk},
        )

    def test_filter_by_generated_by(self):
        self.assertEqual(
            self._ids({"generated_by": self.other_staff.username}),
            {self.uniform_batch.pk},
        )

    def test_filter_by_created_date_range(self):
        today = timezone.localdate().isoformat()
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()

        self.assertEqual(
            self._ids({"date_gte": today, "date_lte": today}),
            {self.library_batch.pk, self.uniform_batch.pk},
        )
        self.assertEqual(self._ids({"date_gte": tomorrow}), set())

    def test_filter_by_total_created_fees_bounds(self):
        self.assertEqual(
            self._ids({"total_created_fees_gte": 100}), {self.library_batch.pk}
        )
        self.assertEqual(
            self._ids({"total_created_fees_lte": 10}), {self.uniform_batch.pk}
        )

    def test_search_by_category_name(self):
        self.assertEqual(
            self._ids({"search": "uniform"}), {self.uniform_batch.pk}
        )

    def test_ordering_by_total_created_fees(self):
        response = self.client.get(self.url, {"ordering": "total_created_fees"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        totals = [row["total_created_fees"] for row in response.data["data"]]
        self.assertEqual(totals, sorted(totals))

    def test_filters_stay_inside_institute_scope(self):
        foreign_institute = make_institute()
        GeneratedFeeBatch.objects.create(
            category=make_category(foreign_institute, name="Library Fee"),
            institute=foreign_institute,
            academic_year=ACADEMIC_YEAR,
            semester=Semester.FIRST,
        )
        self.client.force_authenticate(user=make_staff(self.institute))

        self.assertEqual(
            self._ids({"category": "library"}), {self.library_batch.pk}
        )
