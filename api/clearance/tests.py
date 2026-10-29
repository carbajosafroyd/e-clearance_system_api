from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from api.clearance.models import (
    ClearanceType,
    ClearanceRequirement,
    StudentClearance,
    StudentClearanceItem,
)
from api.institutes.models import Institute
from api.programs.models import Program
from api.rbac.roles import Roles
from api.schools.models import School
from api.students.models import Student
from api.users.models import User


class ClearanceAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Create basic hierarchy
        self.school = School.objects.create(school_name="DNSC", short_name="DNSC", location="Panabo City")
        self.institute1 = Institute.objects.create(institute_name="Institute of Computing", acronym="IC", school=self.school)
        self.institute2 = Institute.objects.create(institute_name="Institute of Leadership", acronym="IL", school=self.school)

        self.program1 = Program.objects.create(
            name="BS in Information Technology",
            acronym="BSIT",
            institute=self.institute1,
        )
        self.program2 = Program.objects.create(
            name="BS in Public Administration",
            acronym="BSPA",
            institute=self.institute2,
        )

        # Fetch or create groups
        self.admin_group, _ = Group.objects.get_or_create(name=Roles.CLEARANCE_ADMIN)
        self.librarian_group, _ = Group.objects.get_or_create(name=Roles.LIBRARIAN)
        self.dean_group, _ = Group.objects.get_or_create(name=Roles.DEAN)
        self.student_group, _ = Group.objects.get_or_create(name=Roles.STUDENT)

        # Admin user
        self.admin_user = User.objects.create_user(
            username="clearance_admin",
            email="admin@dnsc.edu.ph",
            password="adminpassword123",
            institute=self.institute1,
            school=self.school,
            is_staff=True,
        )
        self.admin_user.groups.add(self.admin_group)

        # Signatory (Librarian)
        self.librarian_user = User.objects.create_user(
            username="librarian_ic",
            email="librarian@dnsc.edu.ph",
            password="libpassword123",
            institute=self.institute1,
            school=self.school,
            is_staff=True,
        )
        self.librarian_user.groups.add(self.librarian_group)

        # Student 1 (Year 3)
        self.student_user = User.objects.create_user(
            username="student_juan",
            email="juan@dnsc.edu.ph",
            password="studentpassword123",
            institute=self.institute1,
            school=self.school,
        )
        self.student_user.groups.add(self.student_group)
        self.student = Student.objects.create(
            s_studentID="2024000001",
            s_fname="Juan",
            s_lname="Dela Cruz",
            s_lvl=3,
            program=self.program1,
            user=self.student_user,
        )

        # Create Clearance Type for Institute 1
        self.clearance_type = ClearanceType.objects.create(
            institute=self.institute1,
            name="1st Sem Clearance",
            academic_year="2026-2027",
            semester="1st",
            is_active=True,
            is_issued=True,
            target_year_levels=[3, 4],
        )

        # Create Requirement
        self.requirement = ClearanceRequirement.objects.create(
            clearance_type=self.clearance_type,
            name="Library Clearance",
            authorizing_role=self.librarian_group,
            order_sequence=1,
        )

        # Student Clearance
        self.student_clearance = StudentClearance.objects.create(
            student=self.student,
            clearance_type=self.clearance_type,
            status=StudentClearance.ClearanceStatus.PENDING,
        )

        # Clearance Item
        self.clearance_item = StudentClearanceItem.objects.create(
            student_clearance=self.student_clearance,
            requirement=self.requirement,
            status=StudentClearanceItem.ItemStatus.PENDING,
        )

    def _extract_results(self, response_data):
        if isinstance(response_data, list):
            return response_data
        if isinstance(response_data, dict):
            if "data" in response_data:
                inner = response_data["data"]
                if isinstance(inner, list):
                    return inner
                if isinstance(inner, dict) and "results" in inner:
                    return inner["results"]
            if "results" in response_data:
                return response_data["results"]
        return response_data

    def test_admin_can_list_clearance_types(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get("/api/v1/clearance-types/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should see clearance type for institute 1
        results = self._extract_results(response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "1st Sem Clearance")

    def test_student_sees_only_eligible_clearance_types(self):
        self.client.force_authenticate(user=self.student_user)
        response = self.client.get("/api/v1/clearance-types/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.data)
        self.assertEqual(len(results), 1)

        # If year level doesn't match, student shouldn't see it
        self.student.s_lvl = 1
        self.student.save()
        response2 = self.client.get("/api/v1/clearance-types/")
        results2 = self._extract_results(response2.data)
        self.assertEqual(len(results2), 0)

    def test_student_can_view_my_clearances(self):
        self.client.force_authenticate(user=self.student_user)
        response = self.client.get("/api/v1/student-clearances/my-clearances/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["student_number"], "2024000001")

    def test_signatory_sees_assigned_clearance_items(self):
        self.client.force_authenticate(user=self.librarian_user)
        response = self.client.get("/api/v1/student-clearance-items/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["requirement_name"], "Library Clearance")

    def test_unauthenticated_request_fails(self):
        response = self.client.get("/api/v1/clearance-types/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_multi_institute_isolation(self):
        # Admin from Institute 2
        admin_ic2 = User.objects.create_user(
            username="admin_ic2",
            email="admin_ic2@dnsc.edu.ph",
            password="adminpassword123",
            institute=self.institute2,
            school=self.school,
            is_staff=True,
        )
        admin_ic2.groups.add(self.admin_group)

        self.client.force_authenticate(user=admin_ic2)
        response = self.client.get("/api/v1/clearance-types/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.data)
        # Should NOT see institute 1 clearance types
        self.assertEqual(len(results), 0)

        # Institute 2 clearance response should only show institute 2
        ct2 = ClearanceType.objects.create(
            institute=self.institute2,
            name="IL Semestral Clearance",
            academic_year="2026-2027",
            semester="1st",
            is_active=True,
            is_issued=True,
        )
        response2 = self.client.get("/api/v1/clearance-types/")
        results2 = self._extract_results(response2.data)
        self.assertEqual(len(results2), 1)
        self.assertEqual(results2[0]["name"], "IL Semestral Clearance")

    def test_role_based_signatory_isolation(self):
        # Dean should not see Library Clearance items
        dean_user = User.objects.create_user(
            username="dean_ic",
            email="dean@dnsc.edu.ph",
            password="deanpassword123",
            institute=self.institute1,
            school=self.school,
            is_staff=True,
        )
        dean_user.groups.add(self.dean_group)

        self.client.force_authenticate(user=dean_user)
        response = self.client.get("/api/v1/student-clearance-items/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.data)
        self.assertEqual(len(results), 0)

    def test_school_wide_admin_can_view_all_institutes(self):
        school_admin_group, _ = Group.objects.get_or_create(name=Roles.SCHOOL_ADMIN)
        school_admin_user = User.objects.create_user(
            username="school_superadmin",
            email="superadmin@dnsc.edu.ph",
            password="superpassword123",
            school=self.school,
            is_staff=True,
        )
        school_admin_user.groups.add(school_admin_group)

        # Create clearance type for Institute 2
        ClearanceType.objects.create(
            institute=self.institute2,
            name="Institute 2 Clearance",
            academic_year="2026-2027",
            semester="1st",
            is_active=True,
            is_issued=True,
        )

        self.client.force_authenticate(user=school_admin_user)
        response = self.client.get("/api/v1/clearance-types/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._extract_results(response.data)
        # School admin should see clearance types from both institute1 and institute2
        self.assertEqual(len(results), 2)

    def test_pruned_routes_return_404(self):
        self.client.force_authenticate(user=self.admin_user)
        # Lockers route was pruned/commented out
        response_lockers = self.client.get("/api/lockers/")
        self.assertEqual(response_lockers.status_code, status.HTTP_404_NOT_FOUND)

        # Attendance route was pruned/commented out
        response_attendance = self.client.get("/api/attendance/")
        self.assertEqual(response_attendance.status_code, status.HTTP_404_NOT_FOUND)

        # Legacy n8n bridge was retired
        response_bridge = self.client.get("/api/v1/bridge/")
        self.assertEqual(response_bridge.status_code, status.HTTP_404_NOT_FOUND)
