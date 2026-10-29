"""
Tests for student complaints.

The things worth protecting: a complaint is never closed in silence, a
student cannot answer their own or edit one after it has been answered, and
the module keeps working with the whole contributions scheme switched off -
that independence is the reason it is a separate app.
"""

from __future__ import annotations

from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from api.fees.tests.factories import (
    make_category,
    make_fee,
    make_institute,
    make_program,
    make_staff,
    make_student,
)
from api.notifications.models import Notification
from core.choices import SystemScope

from . import selectors, services
from .models import Complaint, ComplaintCategory

ALL_ON = {"contributions": True, "contribution_points": True, "complaints": True}
COMPLAINTS_OFF = {**ALL_ON, "complaints": False}
POINTS_GONE = {"contributions": False, "contribution_points": False, "complaints": True}


def make_complaint(student, **overrides) -> Complaint:
    defaults = {
        "system": SystemScope.COLLECTION,
        "category": ComplaintCategory.MIS_ISSUED_FEE,
        "title": "Charged for a fee I already paid",
        "description": "I paid this last semester and have the receipt for it.",
    }
    defaults.update(overrides)
    return Complaint.objects.create(student=student, **defaults)


class ComplaintWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.student = make_student(cls.program)
        cls.officer = make_staff(cls.institute)

    def test_resolving_tells_the_student_what_was_done(self):
        complaint = make_complaint(self.student)

        resolved = services.resolve_complaint(
            complaint=complaint,
            actor=self.officer,
            response="The duplicate was cancelled and your balance is corrected.",
        )

        self.assertEqual(resolved.status, Complaint.Status.RESOLVED)
        self.assertEqual(resolved.handled_by, self.officer)
        self.assertIsNotNone(resolved.handled_at)

        notification = Notification.objects.get(user=self.student.user)
        self.assertEqual(
            notification.type, Notification.NotificationType.COMPLAINT_RESOLVED
        )
        self.assertIn("balance is corrected", notification.message)
        self.assertEqual(notification.data["complaint_id"], complaint.pk)

    def test_rejecting_notifies_too(self):
        """A complaint that vanishes is how somebody learns not to file one."""
        complaint = make_complaint(self.student)

        services.reject_complaint(
            complaint=complaint,
            actor=self.officer,
            response="The charge is correct - the receipt is for a different term.",
        )

        notification = Notification.objects.get(user=self.student.user)
        self.assertEqual(
            notification.type, Notification.NotificationType.COMPLAINT_REJECTED
        )
        self.assertIn("different term", notification.message)

    def test_an_answer_is_required_either_way(self):
        for handler in (services.resolve_complaint, services.reject_complaint):
            complaint = make_complaint(self.student)
            with self.assertRaises(ValidationError):
                handler(complaint=complaint, actor=self.officer, response="   ")
            complaint.refresh_from_db()
            self.assertEqual(complaint.status, Complaint.Status.SUBMITTED)

    def test_acknowledging_is_not_an_answer(self):
        complaint = make_complaint(self.student)

        acknowledged = services.acknowledge_complaint(
            complaint=complaint, actor=self.officer
        )

        self.assertEqual(acknowledged.status, Complaint.Status.ACKNOWLEDGED)
        self.assertTrue(acknowledged.is_open)
        self.assertIsNone(acknowledged.response)

    def test_acknowledging_twice_is_harmless(self):
        complaint = make_complaint(self.student)
        services.acknowledge_complaint(complaint=complaint, actor=self.officer)
        services.acknowledge_complaint(complaint=complaint, actor=self.officer)

        self.assertEqual(Notification.objects.filter(user=self.student.user).count(), 1)

    def test_an_answered_complaint_cannot_be_answered_again(self):
        complaint = make_complaint(self.student)
        services.resolve_complaint(
            complaint=complaint, actor=self.officer, response="Done."
        )

        with self.assertRaises(ValidationError):
            services.reject_complaint(
                complaint=complaint, actor=self.officer, response="Actually, no."
            )

    def test_withdrawing_closes_it_quietly(self):
        """The student did it themselves; notifying them is noise."""
        complaint = make_complaint(self.student)

        withdrawn = services.withdraw_complaint(
            complaint=complaint, actor=self.student.user, reason="Sorted at the office."
        )

        self.assertEqual(withdrawn.status, Complaint.Status.WITHDRAWN)
        self.assertFalse(Notification.objects.filter(user=self.student.user).exists())

    def test_a_complaint_outlives_the_fee_it_is_about(self):
        """Cancelling the wrong fee is a good outcome, not a reason to forget."""
        fee = make_fee(self.student, make_category(self.institute))
        complaint = make_complaint(self.student, related_fee=fee)

        fee.delete()
        complaint.refresh_from_db()

        self.assertIsNone(complaint.related_fee_id)
        self.assertEqual(complaint.title, "Charged for a fee I already paid")


class ComplaintApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.student = make_student(cls.program)
        cls.other_student = make_student(cls.program)
        cls.officer = make_staff(cls.institute)

        cls.outsider_institute = make_institute()
        cls.outsider = make_staff(cls.outsider_institute)

    def setUp(self):
        self.client = APIClient()

    def _file(self, **overrides):
        payload = {
            "system": SystemScope.COLLECTION,
            "category": ComplaintCategory.MIS_ISSUED_FEE,
            "title": "Charged for a fee I already paid",
            "description": "I paid this last semester and I still have the receipt.",
        }
        payload.update(overrides)
        return self.client.post("/api/v1/complaints/", payload, format="json")

    def test_a_student_can_file_one(self):
        self.client.force_authenticate(self.student.user)

        response = self._file()

        self.assertEqual(response.status_code, 201)
        complaint = Complaint.objects.get(pk=response.data["id"])
        self.assertEqual(complaint.student, self.student)
        self.assertEqual(complaint.status, Complaint.Status.SUBMITTED)

    def test_a_one_line_complaint_is_refused(self):
        """Not gatekeeping - an officer cannot act on 'fee wrong'."""
        self.client.force_authenticate(self.student.user)

        response = self._file(title="fee bad", description="wrong")

        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.data)
        self.assertIn("description", response.data)

    def test_a_student_cannot_complain_about_somebody_elses_fee(self):
        fee = make_fee(self.other_student, make_category(self.institute))
        self.client.force_authenticate(self.student.user)

        response = self._file(related_fee=fee.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn("related_fee", response.data)

    def test_staff_cannot_file_on_a_students_behalf(self):
        """No staff role holds `add_complaint` - answering is not filing."""
        self.client.force_authenticate(self.officer)

        response = self._file()

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Complaint.objects.exists())

    def test_a_student_sees_only_their_own(self):
        make_complaint(self.student)
        make_complaint(self.other_student)
        self.client.force_authenticate(self.student.user)

        response = self.client.get("/api/v1/complaints/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_items"], 1)

    def test_staff_see_their_institute(self):
        make_complaint(self.student)
        make_complaint(self.other_student)
        self.client.force_authenticate(self.officer)

        response = self.client.get("/api/v1/complaints/")

        self.assertEqual(response.data["total_items"], 2)

    def test_another_institute_sees_none_of_it(self):
        make_complaint(self.student)
        self.client.force_authenticate(self.outsider)

        response = self.client.get("/api/v1/complaints/")

        self.assertEqual(response.data["total_items"], 0)

    def test_a_student_cannot_resolve_their_own(self):
        complaint = make_complaint(self.student)
        self.client.force_authenticate(self.student.user)

        response = self.client.post(
            f"/api/v1/complaints/{complaint.pk}/resolve/",
            {"response": "I have decided I am right."},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        complaint.refresh_from_db()
        self.assertEqual(complaint.status, Complaint.Status.SUBMITTED)

    def test_an_officer_resolves_and_the_student_is_told(self):
        complaint = make_complaint(self.student)
        self.client.force_authenticate(self.officer)

        response = self.client.post(
            f"/api/v1/complaints/{complaint.pk}/resolve/",
            {"response": "Cancelled the duplicate; your balance is corrected."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Complaint.Status.RESOLVED)
        self.assertTrue(
            Notification.objects.filter(
                user=self.student.user,
                type=Notification.NotificationType.COMPLAINT_RESOLVED,
            ).exists()
        )

    def test_an_answer_cannot_be_left_blank_over_http(self):
        complaint = make_complaint(self.student)
        self.client.force_authenticate(self.officer)

        response = self.client.post(
            f"/api/v1/complaints/{complaint.pk}/resolve/", {}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_editing_stops_once_it_has_been_answered(self):
        complaint = make_complaint(self.student)
        services.resolve_complaint(
            complaint=complaint, actor=self.officer, response="Handled."
        )
        self.client.force_authenticate(self.student.user)

        response = self.client.patch(
            f"/api/v1/complaints/{complaint.pk}/",
            {"description": "Actually it was a different fee entirely, sorry."},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_only_the_filer_may_withdraw(self):
        complaint = make_complaint(self.student)
        self.client.force_authenticate(self.officer)

        response = self.client.post(
            f"/api/v1/complaints/{complaint.pk}/withdraw/", {}, format="json"
        )

        self.assertEqual(response.status_code, 403)

    def test_the_summary_zero_fills_quiet_systems(self):
        make_complaint(self.student)
        self.client.force_authenticate(self.officer)

        response = self.client.get("/api/v1/complaints/summary/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["open"], 1)
        systems = {row["system"] for row in response.data["by_system"]}
        self.assertIn(SystemScope.LOCKER, systems)

    def test_students_cannot_read_the_queue_summary(self):
        self.client.force_authenticate(self.student.user)

        response = self.client.get("/api/v1/complaints/summary/")

        self.assertEqual(response.status_code, 403)


class ComplaintFeatureFlagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.student = make_student(cls.program)
        cls.officer = make_staff(cls.institute)

    def setUp(self):
        self.client = APIClient()

    @override_settings(FEATURES=COMPLAINTS_OFF)
    def test_the_module_refuses_when_it_is_switched_off(self):
        self.client.force_authenticate(self.student.user)

        response = self.client.get("/api/v1/complaints/")

        self.assertEqual(response.status_code, 403)
        self.assertIn("not enabled", response.data["detail"].lower())

    @override_settings(FEATURES=POINTS_GONE)
    def test_complaints_survive_the_whole_points_scheme_being_off(self):
        """The point of the split: a claim is owed an answer either way."""
        self.client.force_authenticate(self.student.user)

        response = self.client.post(
            "/api/v1/complaints/",
            {
                "system": SystemScope.COLLECTION,
                "category": ComplaintCategory.MIS_ISSUED_FEE,
                "title": "Charged for a fee I already paid",
                "description": "I paid this last semester and have the receipt.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)


class ComplaintSelectorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.student = make_student(cls.program)
        cls.officer = make_staff(cls.institute)

    def test_the_summary_counts_each_ending_separately(self):
        make_complaint(self.student)
        services.acknowledge_complaint(
            complaint=make_complaint(self.student), actor=self.officer
        )
        services.resolve_complaint(
            complaint=make_complaint(self.student),
            actor=self.officer,
            response="Fixed.",
        )
        services.reject_complaint(
            complaint=make_complaint(self.student),
            actor=self.officer,
            response="The charge is correct.",
        )

        summary = selectors.complaint_summary(institute_id=self.institute.pk)

        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["open"], 2)
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(summary["rejected"], 1)

    def test_the_category_breakdown_leads_with_the_commonest(self):
        make_complaint(self.student, category=ComplaintCategory.MIS_ISSUED_FEE)
        make_complaint(self.student, category=ComplaintCategory.MIS_ISSUED_FEE)
        make_complaint(self.student, category=ComplaintCategory.SERVICE_ISSUE)

        rows = selectors.category_breakdown(institute_id=self.institute.pk)

        self.assertEqual(rows[0]["category"], ComplaintCategory.MIS_ISSUED_FEE)
        self.assertEqual(rows[0]["total"], 2)
