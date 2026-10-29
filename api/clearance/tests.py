from unittest.mock import patch
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.clearance.models import (
    ClearanceType,
    ClearanceRequirement,
    StudentClearance,
    StudentClearanceItem,
)
from api.institutes.models import Institute
from api.notifications.models import Notification
from api.programs.models import Program
from api.rbac.roles import Roles
from api.schools.models import School
from api.students.models import Student
from api.users.models import User
from api.utils.cloudinary.cloudinary_utils import UploadedImage


def _extract_results(response_data):
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


class SequentialClearanceTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.school = School.objects.create(school_name="DNSC", short_name="DNSC", location="Panabo City")
        self.institute = Institute.objects.create(institute_name="Institute of Computing", acronym="IC", school=self.school)
        self.program = Program.objects.create(name="BS in Information Technology", acronym="BSIT", institute=self.institute)

        self.librarian_group, _ = Group.objects.get_or_create(name=Roles.LIBRARIAN)
        self.dean_group, _ = Group.objects.get_or_create(name=Roles.DEAN)
        self.student_group, _ = Group.objects.get_or_create(name=Roles.STUDENT)

        self.librarian_user = User.objects.create_user(
            username="librarian_user",
            email="librarian@dnsc.edu.ph",
            password="password123",
            institute=self.institute,
            school=self.school,
            is_staff=True,
        )
        self.librarian_user.groups.add(self.librarian_group)

        self.dean_user = User.objects.create_user(
            username="dean_user",
            email="dean@dnsc.edu.ph",
            password="password123",
            institute=self.institute,
            school=self.school,
            is_staff=True,
        )
        self.dean_user.groups.add(self.dean_group)

        self.student_user = User.objects.create_user(
            username="student_user",
            email="student@dnsc.edu.ph",
            password="password123",
            institute=self.institute,
            school=self.school,
        )
        self.student_user.groups.add(self.student_group)

        self.student = Student.objects.create(
            s_studentID="2024000099",
            s_fname="Maria",
            s_lname="Clara",
            s_lvl=3,
            program=self.program,
            user=self.student_user,
        )

        self.clearance_type = ClearanceType.objects.create(
            institute=self.institute,
            name="2nd Sem Clearance",
            academic_year="2026-2027",
            semester="2nd",
            is_active=True,
            is_issued=True,
            target_year_levels=[3],
        )

        # Step 1: Library
        self.req_library = ClearanceRequirement.objects.create(
            clearance_type=self.clearance_type,
            name="Library Clearance",
            authorizing_role=self.librarian_group,
            order_sequence=1,
        )

        # Step 2: Dean
        self.req_dean = ClearanceRequirement.objects.create(
            clearance_type=self.clearance_type,
            name="Dean Clearance",
            authorizing_role=self.dean_group,
            order_sequence=2,
        )

        self.student_clearance = StudentClearance.objects.create(
            student=self.student,
            clearance_type=self.clearance_type,
            status=StudentClearance.ClearanceStatus.PENDING,
        )

        self.item_library = StudentClearanceItem.objects.create(
            student_clearance=self.student_clearance,
            requirement=self.req_library,
            status=StudentClearanceItem.ItemStatus.PENDING,
        )

        self.item_dean = StudentClearanceItem.objects.create(
            student_clearance=self.student_clearance,
            requirement=self.req_dean,
            status=StudentClearanceItem.ItemStatus.PENDING,
        )

    def test_sequential_signing_blocks_out_of_order_approval(self):
        # Dean tries to clear Step 2 before Step 1 is cleared
        self.client.force_authenticate(user=self.dean_user)
        response = self.client.patch(
            f"/api/v1/student-clearance-items/{self.item_dean.id}/",
            {"status": StudentClearanceItem.ItemStatus.CLEARED},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Sequential clearance locked", str(response.data))
        self.assertIn("Library Clearance", str(response.data))

        # Check item remains pending in DB
        self.item_dean.refresh_from_db()
        self.assertEqual(self.item_dean.status, StudentClearanceItem.ItemStatus.PENDING)

    def test_sequential_signing_and_state_transitions(self):
        # 1. Clear Step 1 (Library)
        self.client.force_authenticate(user=self.librarian_user)
        response1 = self.client.patch(
            f"/api/v1/student-clearance-items/{self.item_library.id}/",
            {"status": StudentClearanceItem.ItemStatus.CLEARED},
        )
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        self.item_library.refresh_from_db()
        self.assertEqual(self.item_library.status, StudentClearanceItem.ItemStatus.CLEARED)
        self.assertEqual(self.item_library.cleared_by_user_id, self.librarian_user.id)
        self.assertIsNotNone(self.item_library.cleared_at)

        # Clearance should now be in_progress
        self.student_clearance.refresh_from_db()
        self.assertEqual(self.student_clearance.status, StudentClearance.ClearanceStatus.IN_PROGRESS)
        self.assertIsNone(self.student_clearance.date_completed)

        # 2. Clear Step 2 (Dean) - should now succeed
        self.client.force_authenticate(user=self.dean_user)
        response2 = self.client.patch(
            f"/api/v1/student-clearance-items/{self.item_dean.id}/",
            {"status": StudentClearanceItem.ItemStatus.CLEARED},
        )
        self.assertEqual(response2.status_code, status.HTTP_200_OK)

        self.item_dean.refresh_from_db()
        self.assertEqual(self.item_dean.status, StudentClearanceItem.ItemStatus.CLEARED)
        self.assertEqual(self.item_dean.cleared_by_user_id, self.dean_user.id)
        self.assertIsNotNone(self.item_dean.cleared_at)

        # All items are cleared -> parent clearance status must be completed
        self.student_clearance.refresh_from_db()
        self.assertEqual(self.student_clearance.status, StudentClearance.ClearanceStatus.COMPLETED)
        self.assertIsNotNone(self.student_clearance.date_completed)

    def test_reverting_item_reverts_completion_status(self):
        # Start with all cleared
        self.item_library.status = StudentClearanceItem.ItemStatus.CLEARED
        self.item_library.cleared_by_user = self.librarian_user
        self.item_library.save()

        self.item_dean.status = StudentClearanceItem.ItemStatus.CLEARED
        self.item_dean.cleared_by_user = self.dean_user
        self.item_dean.save()

        self.student_clearance.status = StudentClearance.ClearanceStatus.COMPLETED
        self.student_clearance.save()

        # Dean flags an issue on Step 2
        self.client.force_authenticate(user=self.dean_user)
        response = self.client.patch(
            f"/api/v1/student-clearance-items/{self.item_dean.id}/",
            {"status": StudentClearanceItem.ItemStatus.HAS_ISSUE, "remarks": "Missing department clearance form"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.item_dean.refresh_from_db()
        self.assertEqual(self.item_dean.status, StudentClearanceItem.ItemStatus.HAS_ISSUE)
        self.assertIsNone(self.item_dean.cleared_by_user)
        self.assertIsNone(self.item_dean.cleared_at)

        # Parent clearance should revert to in_progress and date_completed to None
        self.student_clearance.refresh_from_db()
        self.assertEqual(self.student_clearance.status, StudentClearance.ClearanceStatus.IN_PROGRESS)
        self.assertIsNone(self.student_clearance.date_completed)

    def test_signatory_dashboard_actionable_filter(self):
        # Step 1 is PENDING, Step 2 is PENDING
        self.client.force_authenticate(user=self.dean_user)

        # Dean with actionable_only=true should see 0 students
        resp_actionable = self.client.get("/api/v1/student-clearance-items/signatory-dashboard/?actionable_only=true")
        self.assertEqual(resp_actionable.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_actionable.data["students_count"], 0)

        # Dean without actionable_only should see the student with is_actionable=False
        resp_all = self.client.get("/api/v1/student-clearance-items/signatory-dashboard/")
        self.assertEqual(resp_all.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_all.data["students_count"], 1)
        dean_items = resp_all.data["assigned_clearances"][0]["items"]
        self.assertFalse(dean_items[0]["is_actionable"])

        # Now librarian clears Step 1
        self.item_library.status = StudentClearanceItem.ItemStatus.CLEARED
        self.item_library.save()

        # Now Dean with actionable_only=true should see the student with is_actionable=True
        resp_actionable_now = self.client.get("/api/v1/student-clearance-items/signatory-dashboard/?actionable_only=true")
        self.assertEqual(resp_actionable_now.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_actionable_now.data["students_count"], 1)
        actionable_items = resp_actionable_now.data["assigned_clearances"][0]["items"]
        self.assertTrue(actionable_items[0]["is_actionable"])

    def test_audit_fields_cannot_be_tampered_by_client(self):
        # First clear step 1 so step 2 is valid
        self.item_library.status = StudentClearanceItem.ItemStatus.CLEARED
        self.item_library.save()

        # Dean attempts to forge cleared_by_user and cleared_at
        self.client.force_authenticate(user=self.dean_user)
        response = self.client.patch(
            f"/api/v1/student-clearance-items/{self.item_dean.id}/",
            {
                "status": StudentClearanceItem.ItemStatus.CLEARED,
                "cleared_by_user": 99999,
                "cleared_at": "1999-01-01T00:00:00Z",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.item_dean.refresh_from_db()
        # Must be Dean user, not 99999
        self.assertEqual(self.item_dean.cleared_by_user_id, self.dean_user.id)
        # Cleared at must be near current time, not 1999
        self.assertNotEqual(self.item_dean.cleared_at.year, 1999)


class DeficiencyAndProofTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.school = School.objects.create(school_name="DNSC", short_name="DNSC", location="Panabo City")
        self.institute = Institute.objects.create(institute_name="Institute of Computing", acronym="IC", school=self.school)
        self.program = Program.objects.create(name="BS in Information Technology", acronym="BSIT", institute=self.institute)

        self.librarian_group, _ = Group.objects.get_or_create(name=Roles.LIBRARIAN)
        self.student_group, _ = Group.objects.get_or_create(name=Roles.STUDENT)

        self.librarian_user = User.objects.create_user(
            username="librarian_test",
            email="librarian_test@dnsc.edu.ph",
            password="password123",
            institute=self.institute,
            school=self.school,
            is_staff=True,
        )
        self.librarian_user.groups.add(self.librarian_group)

        # Student 1
        self.student_user1 = User.objects.create_user(
            username="student_one",
            email="student1@dnsc.edu.ph",
            password="password123",
            institute=self.institute,
            school=self.school,
        )
        self.student_user1.groups.add(self.student_group)
        self.student1 = Student.objects.create(
            s_studentID="2024111111",
            s_fname="Pedro",
            s_lname="Penduko",
            s_lvl=3,
            program=self.program,
            user=self.student_user1,
        )

        # Student 2
        self.student_user2 = User.objects.create_user(
            username="student_two",
            email="student2@dnsc.edu.ph",
            password="password123",
            institute=self.institute,
            school=self.school,
        )
        self.student_user2.groups.add(self.student_group)
        self.student2 = Student.objects.create(
            s_studentID="2024222222",
            s_fname="Cardo",
            s_lname="Dalisay",
            s_lvl=3,
            program=self.program,
            user=self.student_user2,
        )

        self.clearance_type = ClearanceType.objects.create(
            institute=self.institute,
            name="1st Sem Clearance",
            academic_year="2026-2027",
            semester="1st",
            is_active=True,
            is_issued=True,
            target_year_levels=[3],
        )

        self.req_library = ClearanceRequirement.objects.create(
            clearance_type=self.clearance_type,
            name="Library Clearance",
            authorizing_role=self.librarian_group,
            order_sequence=1,
        )

        self.student_clearance1 = StudentClearance.objects.create(
            student=self.student1,
            clearance_type=self.clearance_type,
            status=StudentClearance.ClearanceStatus.PENDING,
        )

        self.item_library1 = StudentClearanceItem.objects.create(
            student_clearance=self.student_clearance1,
            requirement=self.req_library,
            status=StudentClearanceItem.ItemStatus.PENDING,
        )

    @patch("api.utils.cloudinary.cloudinary_utils.upload_image_versioned")
    def test_student_can_upload_proof(self, mock_upload):
        mock_upload.return_value = UploadedImage(
            secure_url="https://res.cloudinary.com/demo/image/upload/proof1.png",
            public_id="clearance/proofs/proof1",
            version=1,
        )

        self.client.force_authenticate(user=self.student_user1)
        test_file = SimpleUploadedFile("receipt.png", b"file_content", content_type="image/png")

        response = self.client.post(
            f"/api/v1/student-clearance-items/{self.item_library1.id}/upload-proof/",
            {"file": test_file, "proof_notes": "Paid library penalty"},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.item_library1.refresh_from_db()
        self.assertEqual(self.item_library1.proof_attachment, "clearance/proofs/proof1")
        self.assertEqual(self.item_library1.cloudinary_version, 1)
        self.assertEqual(self.item_library1.proof_notes, "Paid library penalty")
        self.assertIsNotNone(self.item_library1.proof_uploaded_at)

        # Signatory should have received a notification
        notif = Notification.objects.filter(
            user=self.librarian_user,
            type=Notification.NotificationType.CLEARANCE_PROOF_SUBMITTED,
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Pedro Penduko", notif.message)
        self.assertIn("Library Clearance", notif.message)

    def test_other_student_cannot_upload_proof_to_different_student(self):
        self.client.force_authenticate(user=self.student_user2)
        test_file = SimpleUploadedFile("hack.png", b"file_content", content_type="image/png")

        response = self.client.post(
            f"/api/v1/student-clearance-items/{self.item_library1.id}/upload-proof/",
            {"file": test_file},
            format="multipart",
        )
        self.assertIn(response.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND])

    def test_student_receives_notification_on_deficiency_issue(self):
        self.client.force_authenticate(user=self.librarian_user)
        response = self.client.patch(
            f"/api/v1/student-clearance-items/{self.item_library1.id}/",
            {
                "status": StudentClearanceItem.ItemStatus.HAS_ISSUE,
                "remarks": "Missing Java Programming Handbook",
                "amount_due": "250.00",
            },
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        notif = Notification.objects.filter(
            user=self.student_user1,
            type=Notification.NotificationType.CLEARANCE_ISSUE,
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Missing Java Programming Handbook", notif.message)
        self.assertIn("250.00", notif.message)

    @patch("api.utils.cloudinary.cloudinary_utils.delete_image")
    def test_student_can_remove_uploaded_proof(self, mock_delete):
        self.item_library1.proof_attachment = "clearance/proofs/proof1"
        self.item_library1.cloudinary_version = 1
        self.item_library1.proof_notes = "Wrong receipt uploaded"
        self.item_library1.save()

        self.client.force_authenticate(user=self.student_user1)
        response = self.client.delete(
            f"/api/v1/student-clearance-items/{self.item_library1.id}/remove-proof/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.item_library1.refresh_from_db()
        self.assertIsNone(self.item_library1.proof_attachment)
        self.assertIsNone(self.item_library1.cloudinary_version)
        self.assertIsNone(self.item_library1.proof_notes)

    def test_cannot_remove_proof_if_item_already_cleared(self):
        self.item_library1.status = StudentClearanceItem.ItemStatus.CLEARED
        self.item_library1.proof_attachment = "clearance/proofs/proof1"
        self.item_library1.save()

        self.client.force_authenticate(user=self.student_user1)
        response = self.client.delete(
            f"/api/v1/student-clearance-items/{self.item_library1.id}/remove-proof/"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already cleared", str(response.data))


class CertificateAndVerificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.school = School.objects.create(school_name="DNSC", short_name="DNSC", location="Panabo City")
        self.institute = Institute.objects.create(institute_name="Institute of Computing", acronym="IC", school=self.school)
        self.program = Program.objects.create(name="BS in Information Technology", acronym="BSIT", institute=self.institute)

        self.admin_group, _ = Group.objects.get_or_create(name=Roles.CLEARANCE_ADMIN)
        self.librarian_group, _ = Group.objects.get_or_create(name=Roles.LIBRARIAN)
        self.dean_group, _ = Group.objects.get_or_create(name=Roles.DEAN)
        self.student_group, _ = Group.objects.get_or_create(name=Roles.STUDENT)

        self.librarian_user = User.objects.create_user(
            username="librarian_cert",
            email="librarian_cert@dnsc.edu.ph",
            password="password123",
            institute=self.institute,
            school=self.school,
            is_staff=True,
        )
        self.librarian_user.groups.add(self.librarian_group)

        self.dean_user = User.objects.create_user(
            username="dean_cert",
            email="dean_cert@dnsc.edu.ph",
            password="password123",
            institute=self.institute,
            school=self.school,
            is_staff=True,
        )
        self.dean_user.groups.add(self.dean_group)

        self.student_user = User.objects.create_user(
            username="student_cert",
            email="student_cert@dnsc.edu.ph",
            password="password123",
            institute=self.institute,
            school=self.school,
        )
        self.student_user.groups.add(self.student_group)
        self.student = Student.objects.create(
            s_studentID="2024777777",
            s_fname="Crisostomo",
            s_lname="Ibarra",
            s_lvl=4,
            program=self.program,
            user=self.student_user,
        )

        self.clearance_type = ClearanceType.objects.create(
            institute=self.institute,
            name="Graduating Clearance",
            academic_year="2026-2027",
            semester="2nd",
            is_active=True,
            is_issued=True,
            target_year_levels=[4],
        )

        self.req1 = ClearanceRequirement.objects.create(
            clearance_type=self.clearance_type,
            name="Library Clearance",
            authorizing_role=self.librarian_group,
            order_sequence=1,
        )
        self.req2 = ClearanceRequirement.objects.create(
            clearance_type=self.clearance_type,
            name="Dean Clearance",
            authorizing_role=self.dean_group,
            order_sequence=2,
        )

        self.student_clearance = StudentClearance.objects.create(
            student=self.student,
            clearance_type=self.clearance_type,
            status=StudentClearance.ClearanceStatus.PENDING,
        )

        self.item1 = StudentClearanceItem.objects.create(
            student_clearance=self.student_clearance,
            requirement=self.req1,
            status=StudentClearanceItem.ItemStatus.PENDING,
        )
        self.item2 = StudentClearanceItem.objects.create(
            student_clearance=self.student_clearance,
            requirement=self.req2,
            status=StudentClearanceItem.ItemStatus.PENDING,
        )

    def test_certificate_forbidden_if_incomplete(self):
        self.client.force_authenticate(user=self.student_user)
        response = self.client.get(f"/api/v1/student-clearances/{self.student_clearance.id}/certificate/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("only available once all clearance requirements are completed", str(response.data))

    def test_certificate_download_succeeds_when_completed(self):
        # Clear step 1
        self.client.force_authenticate(user=self.librarian_user)
        self.client.patch(
            f"/api/v1/student-clearance-items/{self.item1.id}/",
            {"status": StudentClearanceItem.ItemStatus.CLEARED},
        )
        # Clear step 2
        self.client.force_authenticate(user=self.dean_user)
        self.client.patch(
            f"/api/v1/student-clearance-items/{self.item2.id}/",
            {"status": StudentClearanceItem.ItemStatus.CLEARED},
        )

        self.student_clearance.refresh_from_db()
        self.assertEqual(self.student_clearance.status, StudentClearance.ClearanceStatus.COMPLETED)

        # Student requests certificate
        self.client.force_authenticate(user=self.student_user)
        response = self.client.get(f"/api/v1/student-clearances/{self.student_clearance.id}/certificate/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("inline; filename=", response["Content-Disposition"])
        # Verify PDF magic bytes
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_public_verification_endpoint(self):
        # Clear both items
        self.item1.status = StudentClearanceItem.ItemStatus.CLEARED
        self.item1.cleared_by_user = self.librarian_user
        self.item1.cleared_at = timezone.now()
        self.item1.save()

        self.item2.status = StudentClearanceItem.ItemStatus.CLEARED
        self.item2.cleared_by_user = self.dean_user
        self.item2.cleared_at = timezone.now()
        self.item2.save()

        self.student_clearance.status = StudentClearance.ClearanceStatus.COMPLETED
        self.student_clearance.date_completed = timezone.now()
        self.student_clearance.save()

        # Unauthenticated request to verify endpoint
        self.client.logout()
        token = str(self.student_clearance.verification_token)
        response = self.client.get(f"/api/v1/student-clearances/verify/{token}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["valid"])
        self.assertEqual(response.data["student_id"], "2024777777")
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(len(response.data["signatories"]), 2)
        self.assertEqual(response.data["signatories"][0]["requirement"], "Library Clearance")

    def test_public_verification_invalid_token(self):
        self.client.logout()
        import uuid
        random_token = str(uuid.uuid4())
        response = self.client.get(f"/api/v1/student-clearances/verify/{random_token}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(response.data["valid"])

    def test_full_clearance_lifecycle_e2e(self):
        # 1. Student checks my clearances
        self.client.force_authenticate(user=self.student_user)
        resp_my = self.client.get("/api/v1/student-clearances/my-clearances/")
        self.assertEqual(resp_my.status_code, status.HTTP_200_OK)
        results = _extract_results(resp_my.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], StudentClearance.ClearanceStatus.PENDING)

        # 2. Student tries to download certificate before completion -> rejected
        resp_cert_early = self.client.get(
            f"/api/v1/student-clearances/{self.student_clearance.id}/certificate/"
        )
        self.assertEqual(resp_cert_early.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Dean tries to sign Step 2 first -> blocked by sequential validation
        self.client.force_authenticate(user=self.dean_user)
        resp_dean_early = self.client.patch(
            f"/api/v1/student-clearance-items/{self.item2.id}/",
            {"status": StudentClearanceItem.ItemStatus.CLEARED},
        )
        self.assertEqual(resp_dean_early.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Sequential clearance locked", str(resp_dean_early.data))

        # 4. Librarian signs Step 1
        self.client.force_authenticate(user=self.librarian_user)
        resp_lib = self.client.patch(
            f"/api/v1/student-clearance-items/{self.item1.id}/",
            {"status": StudentClearanceItem.ItemStatus.CLEARED},
        )
        self.assertEqual(resp_lib.status_code, status.HTTP_200_OK)

        # Student clearance moves to in_progress
        self.student_clearance.refresh_from_db()
        self.assertEqual(self.student_clearance.status, StudentClearance.ClearanceStatus.IN_PROGRESS)

        # 5. Dean now signs Step 2 -> succeeds
        self.client.force_authenticate(user=self.dean_user)
        resp_dean = self.client.patch(
            f"/api/v1/student-clearance-items/{self.item2.id}/",
            {"status": StudentClearanceItem.ItemStatus.CLEARED},
        )
        self.assertEqual(resp_dean.status_code, status.HTTP_200_OK)

        # Student clearance automatically transitions to completed
        self.student_clearance.refresh_from_db()
        self.assertEqual(self.student_clearance.status, StudentClearance.ClearanceStatus.COMPLETED)
        self.assertIsNotNone(self.student_clearance.date_completed)

        # 6. Student downloads certificate -> returns valid PDF
        self.client.force_authenticate(user=self.student_user)
        resp_cert = self.client.get(
            f"/api/v1/student-clearances/{self.student_clearance.id}/certificate/"
        )
        self.assertEqual(resp_cert.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_cert["Content-Type"], "application/pdf")
        self.assertTrue(resp_cert.content.startswith(b"%PDF"))

        # 7. Public QR verify lookup succeeds without auth
        self.client.logout()
        token = str(self.student_clearance.verification_token)
        resp_verify = self.client.get(f"/api/v1/student-clearances/verify/{token}/")
        self.assertEqual(resp_verify.status_code, status.HTTP_200_OK)
        self.assertTrue(resp_verify.data["valid"])
        self.assertEqual(resp_verify.data["status"], "completed")
        self.assertEqual(len(resp_verify.data["signatories"]), 2)
