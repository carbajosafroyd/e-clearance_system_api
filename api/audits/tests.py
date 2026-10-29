from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from api.rbac.roles import Roles
from api.rbac.services import assign_role

from .models import AuditLog
from .services import AuditLogService

User = get_user_model()


class AuditLogPermissionTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(username="staff1", password="pass1234")
        assign_role(self.staff, Roles.CLEARANCE_ADMIN)

        self.student = User.objects.create_user(username="student1", password="pass1234")
        assign_role(self.student, Roles.STUDENT)

        AuditLogService.log_action(
            system=AuditLog.System.COLLECTION,
            entity_type="Payment", entity_id=1, action=AuditLog.Action.CREATE,
            actor_type="staff", actor_id=self.staff.id, new_values={"amount": 500},
        )
        self.list_url = reverse("audit-logs-list")

    def test_staff_can_list(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_student_cannot_list(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_list(self):
        response = self.client.get(self.list_url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class AuditLogFilterTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="staff2", password="pass1234",
            first_name="Juan", last_name="Cruz",
        )
        assign_role(self.staff, Roles.CLEARANCE_ADMIN)
        self.other = User.objects.create_user(
            username="staff2b", password="pass1234",
            first_name="Maria", last_name="Santos",
        )
        self.client.force_authenticate(self.staff)

        AuditLogService.log_action(
            system=AuditLog.System.COLLECTION,
            entity_type="Payment", entity_id=1, action=AuditLog.Action.CREATE,
            actor_type="staff", actor_id=self.staff.id,
        )
        AuditLogService.log_action(
            system=AuditLog.System.LOCKER,
            entity_type="Fee", entity_id=2, action=AuditLog.Action.UPDATE,
            actor_type="staff", actor_id=self.other.id, new_values={"status": "waived"},
        )
        AuditLogService.log_action(
            system=AuditLog.System.VOTING,
            entity_type="Fee", entity_id=3, action=AuditLog.Action.DELETE,
            actor_type="staff", actor_id=self.other.id, old_values={"status": "x"},
        )
        self.list_url = reverse("audit-logs-list")

    def results(self, response):
        return response.data["data"]

    def test_filter_by_entity_type(self):
        response = self.client.get(self.list_url, {"entity_type": "Payment"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self.results(response)
        self.assertEqual(len(results), 1)
        self.assertTrue(all(r["entity_type"] == "Payment" for r in results))

    def test_filter_invalid_action_is_rejected(self):
        # A typo should be visible, not read as "no activity".
        response = self.client.get(self.list_url, {"action": "nonexistent"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_filter_by_actor_id(self):
        response = self.client.get(self.list_url, {"actor_id": self.staff.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(self.results(response)), 1)

    def test_filter_by_system(self):
        response = self.client.get(self.list_url, {"system": AuditLog.System.LOCKER})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self.results(response)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["system"], "locker")

    def test_multiple_actions_comma_separated(self):
        response = self.client.get(self.list_url, {"action": "create,delete"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        actions = {r["action"] for r in self.results(response)}
        self.assertEqual(actions, {"create", "delete"})

    def test_multiple_systems_repeated_param(self):
        response = self.client.get(
            f"{self.list_url}?system=locker&system=voting"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        systems = {r["system"] for r in self.results(response)}
        self.assertEqual(systems, {"locker", "voting"})

    def test_actor_search_by_name(self):
        response = self.client.get(self.list_url, {"actor": "maria san"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self.results(response)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["actor_id"] == self.other.id for r in results))

    def test_actor_search_by_username(self):
        response = self.client.get(self.list_url, {"actor": "staff2b"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(self.results(response)), 2)

    def test_actor_search_no_match_returns_empty(self):
        response = self.client.get(self.list_url, {"actor": "nobody-here"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(self.results(response)), 0)

    def test_list_rows_carry_actor_names(self):
        response = self.client.get(self.list_url, {"entity_type": "Payment"})
        row = self.results(response)[0]
        self.assertEqual(row["actor_username"], "staff2")
        self.assertEqual(row["actor_full_name"], "Juan Cruz")

    def test_invalid_date_is_rejected(self):
        response = self.client.get(self.list_url, {"date_from": "not-a-date"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_backwards_date_range_is_rejected(self):
        response = self.client.get(
            self.list_url, {"date_from": "2026-06-30", "date_to": "2026-01-01"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AuditLogCacheTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(username="staff3", password="pass1234")
        assign_role(self.staff, Roles.CLEARANCE_ADMIN)
        self.client.force_authenticate(self.staff)
        self.list_url = reverse("audit-logs-list")

    def test_list_cache_hit_then_invalidation_on_write(self):
        # first call - miss, populates cache
        first = self.client.get(self.list_url, {"current_page": "1", "per_page": "10"})
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        version_key = "AUDITLOG_list_version"
        version_before = cache.get(version_key, "1")

        # second call - should hit cache (same version)
        second = self.client.get(self.list_url, {"current_page": "1", "per_page": "10"})
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data, second.data)

        # a write bumps the version, invalidating the previous cache key
        AuditLogService.log_action(
            system=AuditLog.System.COLLECTION,
            entity_type="Payment", entity_id=99, action=AuditLog.Action.CREATE,
            actor_type="staff", actor_id=self.staff.id,
        )
        version_after = cache.get(version_key, "1")
        self.assertNotEqual(version_before, version_after)

        third = self.client.get(self.list_url, {"current_page": "1", "per_page": "10"})
        self.assertEqual(third.status_code, status.HTTP_200_OK)
        self.assertEqual(len(third.data["data"]), 1)


class AuditLogRetrieveTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(username="staff4", password="pass1234")
        assign_role(self.staff, Roles.CLEARANCE_ADMIN)
        self.client.force_authenticate(self.staff)

        self.audit_log = AuditLogService.log_action(
            system=AuditLog.System.COLLECTION,
            entity_type="Payment", entity_id=5, action=AuditLog.Action.APPROVE,
            actor_type="staff", actor_id=self.staff.id,
            old_values={"status": "pending"}, new_values={"status": "approved"},
        )

    def test_retrieve_returns_detailed_fields(self):
        url = reverse("audit-logs-detail", args=[self.audit_log.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("old_values", response.data)
        self.assertIn("new_values", response.data)

    def test_retrieve_resolves_actor_names(self):
        url = reverse("audit-logs-detail", args=[self.audit_log.id])
        response = self.client.get(url)
        self.assertEqual(response.data["actor_username"], "staff4")

    def test_retrieve_not_found(self):
        url = reverse("audit-logs-detail", args=[999999])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AuditLogReportTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="reporter", password="pass1234",
            first_name="Ana", last_name="Reyes",
        )
        assign_role(self.staff, Roles.CLEARANCE_ADMIN)

        self.student = User.objects.create_user(username="student9", password="pass1234")
        assign_role(self.student, Roles.STUDENT)

        for entity_id in (1, 2):
            AuditLogService.log_action(
                system=AuditLog.System.COLLECTION,
                entity_type="Payment", entity_id=entity_id,
                action=AuditLog.Action.CREATE,
                actor_type="staff", actor_id=self.staff.id,
            )
        AuditLogService.log_action(
            system=AuditLog.System.LOCKER,
            entity_type="Fee", entity_id=3, action=AuditLog.Action.DELETE,
            actor_type="staff", actor_id=self.staff.id, old_values={"status": "x"},
        )

        self.url = reverse("audit-logs-report")
        self.client.force_authenticate(self.staff)

    def report(self, response):
        return response.data

    def test_report_aggregates_and_records(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        report = self.report(response)

        self.assertEqual(report["summary"]["total_entries"], 3)
        self.assertFalse(report["summary"]["records_truncated"])

        by_action = {row["value"]: row["count"] for row in report["by_action"]}
        self.assertEqual(by_action["create"], 2)
        self.assertEqual(by_action["delete"], 1)
        # Zero-filled: every enum value appears even with no entries.
        self.assertEqual(by_action["approve"], 0)

        by_system = {row["value"]: row["count"] for row in report["by_system"]}
        self.assertEqual(by_system["collection"], 2)

        top = report["top_actors"][0]
        self.assertEqual(top["actor_username"], "reporter")
        self.assertEqual(top["actor_full_name"], "Ana Reyes")
        self.assertEqual(top["entry_count"], 3)

        self.assertEqual(len(report["records"]), 3)
        self.assertEqual(report["records"][0]["actor_username"], "reporter")

    def test_report_respects_filters(self):
        response = self.client.get(self.url, {"system": "collection"})
        report = self.report(response)
        self.assertEqual(report["summary"]["total_entries"], 2)
        self.assertEqual(report["applied_filters"]["systems"], "collection")

    def test_report_rejects_unknown_choice(self):
        response = self.client.get(self.url, {"actor_type": "alien"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_cannot_see_report(self):
        self.client.force_authenticate(self.student)
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_anonymous_cannot_see_report(self):
        self.client.force_authenticate(None)
        self.assertIn(
            self.client.get(self.url).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_pdf_export(self):
        response = self.client.get(reverse("audit-logs-report-pdf"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_excel_export(self):
        from io import BytesIO

        from openpyxl import load_workbook

        response = self.client.get(reverse("audit-logs-report-excel"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        workbook = load_workbook(BytesIO(response.content))
        self.assertIn("Summary", workbook.sheetnames)
        self.assertIn("Entries", workbook.sheetnames)
        entries = workbook["Entries"]
        # Header + 3 entries + totals line.
        self.assertEqual(entries.max_row, 5)

