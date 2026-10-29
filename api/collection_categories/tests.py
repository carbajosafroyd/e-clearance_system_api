from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.audits.models import AuditLog
from api.fees.tests.factories import (
    ACADEMIC_YEAR,
    SEMESTER,
    make_category,
    make_exemption,
    make_institute,
    make_program,
    make_staff,
    make_student,
)

from .models import FeeExemptedStudent


class FeeExemptedStudentBulkCreateTests(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        # Superuser so DjangoModelPermissions is satisfied; institute still
        # set because the serializer scopes everything to it.
        self.staff = make_staff(self.institute, superuser=True)
        self.students = [make_student(self.program) for _ in range(3)]
        self.client.force_authenticate(self.staff)
        self.url = reverse("exempted-fee-students-bulk")

    def _payload(self, school_ids, **overrides):
        payload = {
            "student_school_ids": school_ids,
            "category": self.category.pk,
            "reason": "Scholarship",
            "academic_year": ACADEMIC_YEAR,
            "semester": SEMESTER,
        }
        payload.update(overrides)
        return payload

    def test_bulk_create_exempts_all_students(self):
        ids = [s.s_studentID for s in self.students]
        response = self.client.post(self.url, self._payload(ids), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["created_count"], 3)
        self.assertEqual(response.data["skipped_count"], 0)
        self.assertEqual(response.data["created"], ids)
        self.assertEqual(
            FeeExemptedStudent.objects.filter(category=self.category).count(), 3
        )

    def test_bulk_create_skips_already_exempted(self):
        make_exemption(self.students[0], self.category)
        ids = [s.s_studentID for s in self.students]

        response = self.client.post(self.url, self._payload(ids), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["created_count"], 2)
        self.assertEqual(
            response.data["skipped_existing"], [self.students[0].s_studentID]
        )
        self.assertEqual(
            FeeExemptedStudent.objects.filter(category=self.category).count(), 3
        )

    def test_bulk_create_dedupes_repeated_ids(self):
        sid = self.students[0].s_studentID
        response = self.client.post(self.url, self._payload([sid, sid]), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["created_count"], 1)

    def test_unknown_school_id_rejects_whole_request(self):
        ids = [self.students[0].s_studentID, "0000-XXXX"]
        response = self.client.post(self.url, self._payload(ids), format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("0000-XXXX", str(response.data["student_school_ids"]))
        self.assertEqual(FeeExemptedStudent.objects.count(), 0)

    def test_student_outside_institute_rejects_whole_request(self):
        other_program = make_program(make_institute())
        outsider = make_student(other_program)
        ids = [self.students[0].s_studentID, outsider.s_studentID]

        response = self.client.post(self.url, self._payload(ids), format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FeeExemptedStudent.objects.count(), 0)

    def test_category_outside_institute_rejected(self):
        foreign_category = make_category(make_institute())
        ids = [self.students[0].s_studentID]

        response = self.client.post(
            self.url, self._payload(ids, category=foreign_category.pk), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FeeExemptedStudent.objects.count(), 0)

    def test_staff_without_model_permission_denied(self):
        # `role=None` is a staff account in no role at all - the deliberately
        # unauthorised case. Every real role that can reach this endpoint
        # holds `bulk_add_feeexemptedstudent`.
        plain_staff = make_staff(self.institute, role=None)
        self.client.force_authenticate(plain_staff)

        response = self.client.post(
            self.url, self._payload([self.students[0].s_studentID]), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(FeeExemptedStudent.objects.count(), 0)

    def test_bulk_create_writes_one_audit_entry(self):
        ids = [s.s_studentID for s in self.students]
        self.client.post(self.url, self._payload(ids), format="json")

        entries = AuditLog.objects.filter(
            entity_type="FeeExemptedStudent", action=AuditLog.Action.CREATE
        )
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().new_values["created_count"], 3)


class FeeExemptedStudentBulkDeleteTests(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        self.staff = make_staff(self.institute, superuser=True)
        self.students = [make_student(self.program) for _ in range(3)]
        self.exemptions = [
            make_exemption(student, self.category) for student in self.students
        ]
        self.client.force_authenticate(self.staff)
        self.url = reverse("exempted-fee-students-bulk-delete")

    def test_bulk_delete_removes_selected_rows(self):
        ids = [e.pk for e in self.exemptions[:2]]
        response = self.client.post(self.url, {"ids": ids}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["deleted_count"], 2)
        self.assertEqual(response.data["skipped_count"], 0)
        self.assertEqual(response.data["deleted"], ids)
        self.assertEqual(FeeExemptedStudent.objects.count(), 1)

    def test_unknown_ids_are_skipped_and_reported(self):
        ids = [self.exemptions[0].pk, 999999]
        response = self.client.post(self.url, {"ids": ids}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["deleted"], [self.exemptions[0].pk])
        self.assertEqual(response.data["skipped_missing"], [999999])
        self.assertEqual(FeeExemptedStudent.objects.count(), 2)

    def test_reposting_the_same_selection_is_safe(self):
        ids = [self.exemptions[0].pk]
        first = self.client.post(self.url, {"ids": ids}, format="json")
        second = self.client.post(self.url, {"ids": ids}, format="json")

        self.assertEqual(first.data["deleted_count"], 1)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["deleted_count"], 0)
        self.assertEqual(second.data["skipped_missing"], ids)

    def test_other_institutes_rows_cannot_be_deleted(self):
        foreign_category = make_category(make_institute())
        foreign_student = make_student(make_program(foreign_category.institute))
        foreign = make_exemption(foreign_student, foreign_category)

        # Non-superuser staff WITH the bulk-delete permission, scoped to
        # their own institute - the foreign row must read as missing, not
        # deleted. The permission is not the boundary here; get_queryset is.
        scoped_staff = make_staff(self.institute)
        self.assertTrue(
            scoped_staff.has_perm(
                "collection_categories.bulk_delete_feeexemptedstudent"
            )
        )
        self.client.force_authenticate(scoped_staff)

        response = self.client.post(self.url, {"ids": [foreign.pk]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["deleted_count"], 0)
        self.assertEqual(response.data["skipped_missing"], [foreign.pk])
        self.assertTrue(FeeExemptedStudent.objects.filter(pk=foreign.pk).exists())

    def test_staff_without_delete_permission_denied(self):
        plain_staff = make_staff(self.institute, role=None)
        self.client.force_authenticate(plain_staff)

        response = self.client.post(
            self.url, {"ids": [self.exemptions[0].pk]}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(FeeExemptedStudent.objects.count(), 3)

    def test_empty_ids_rejected(self):
        response = self.client.post(self.url, {"ids": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_delete_writes_one_audit_entry_with_snapshot(self):
        ids = [e.pk for e in self.exemptions[:2]]
        self.client.post(self.url, {"ids": ids}, format="json")

        entries = AuditLog.objects.filter(
            entity_type="FeeExemptedStudent", action=AuditLog.Action.DELETE
        )
        self.assertEqual(entries.count(), 1)
        old_values = entries.first().old_values
        self.assertEqual(old_values["deleted_count"], 2)
        self.assertEqual(len(old_values["deleted_exemptions"]), 2)
        self.assertEqual(
            old_values["deleted_exemptions"][0]["student_school_id"],
            self.students[0].s_studentID,
        )


class FeeExemptedStudentListFilterTests(APITestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.staff = make_staff(self.institute, superuser=True)
        self.category_a = make_category(self.institute)
        self.category_b = make_category(self.institute)
        self.student = make_student(self.program)
        self.other_student = make_student(self.program)
        make_exemption(self.student, self.category_a)
        make_exemption(self.other_student, self.category_b)
        self.client.force_authenticate(self.staff)
        self.url = reverse("exempted-fee-students-list")

    def test_filter_by_multiple_categories(self):
        response = self.client.get(
            self.url,
            {"category__in": f"{self.category_a.pk},{self.category_b.pk}"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 2)

    def test_filter_by_single_category(self):
        response = self.client.get(self.url, {"category": self.category_a.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)

    def test_search_by_student_name(self):
        response = self.client.get(self.url, {"search": self.student.s_lname})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)

    def test_search_by_full_displayed_name_with_middle_name(self):
        """Regression: SearchFilter ANDs every typed word, so the middle name
        and suffix must be searchable or the name shown on screen finds
        nothing."""
        self.student.s_fname = "Juan"
        self.student.s_mname = "Santos"
        self.student.s_lname = "Dela Cruz"
        self.student.s_suffix = "Jr."
        self.student.save()

        response = self.client.get(self.url, {"search": "Juan Santos Dela Cruz Jr."})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)

    def test_search_by_category_name(self):
        response = self.client.get(
            self.url, {"search": self.category_a.category_name}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)

    def test_filter_by_year_level(self):
        self.student.s_lvl = 3
        self.student.save()

        response = self.client.get(self.url, {"year_level": 3})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)

    def test_filter_by_program(self):
        other_program = make_program(self.institute)
        self.other_student.program = other_program
        self.other_student.save()

        response = self.client.get(self.url, {"program_id": self.program.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)

    def test_filter_by_student_school_id(self):
        response = self.client.get(
            self.url, {"student_school_id": self.student.s_studentID}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)

    def test_filter_by_reason_icontains(self):
        FeeExemptedStudent.objects.filter(student=self.student).update(
            reason="Medical waiver"
        )
        response = self.client.get(self.url, {"reason": "medical"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["data"]), 1)

    def test_filter_by_created_date_range(self):
        today = timezone.localdate().isoformat()
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()

        both = self.client.get(self.url, {"created_gte": today, "created_lte": today})
        self.assertEqual(both.status_code, status.HTTP_200_OK)
        self.assertEqual(len(both.data["data"]), 2)

        none = self.client.get(self.url, {"created_gte": tomorrow})
        self.assertEqual(none.status_code, status.HTTP_200_OK)
        self.assertEqual(len(none.data["data"]), 0)
