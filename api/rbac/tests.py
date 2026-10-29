"""
Tests for the RBAC layer itself.

Two things matter here and nothing else does: that `roles.py` names only
permissions that really exist (a typo there silently grants nothing), and
that `ActionPermission` fails closed.
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.views import APIView

from api.users.models import User
from api.institutes.models import Institute
from api.schools.models import School

from core import features

from .permissions import (
    ALLOW_ANY,
    DEFAULT,
    DENY,
    ActionPermission,
    AnyOf,
    Feature,
    crud,
    gate,
    user_permission_codenames,
)
from .roles import BASELINE, ROLE_DEFINITIONS, Roles, resolve_permissions
from .services import assign_role, expand, sync_roles


class RoleCatalogTests(TestCase):
    def test_every_permission_named_in_roles_exists(self):
        """A typo in roles.py is silent at runtime - it just grants nothing."""
        missing = set()
        for role in ROLE_DEFINITIONS:
            _, unresolved = expand(resolve_permissions(role))
            missing |= unresolved

        self.assertEqual(
            missing,
            set(),
            "roles.py names permissions that no model registers. Add them to "
            "the model's Meta.permissions and run makemigrations, or fix the "
            "codename.",
        )

    def test_seeding_is_idempotent(self):
        existing = set(Group.objects.values_list("name", flat=True))
        first = sync_roles()
        second = sync_roles()

        self.assertEqual(set(first.created_groups), set(ROLE_DEFINITIONS) - existing)
        self.assertEqual(second.created_groups, [])
        self.assertEqual(second.updated_groups, [])
        self.assertEqual(len(second.unchanged_groups), len(ROLE_DEFINITIONS))

    def test_every_role_holds_the_baseline(self):
        """BASELINE is folded in by resolve_permissions, not by each RoleSpec."""
        for role in ROLE_DEFINITIONS:
            resolved = resolve_permissions(role)
            for permission in BASELINE:
                self.assertIn(permission, resolved, f"{role} lost {permission}")

    def test_a_role_that_inherits_gets_the_parent_permissions(self):
        admin = resolve_permissions(Roles.CMS_ADMIN)
        staff = resolve_permissions(Roles.CMS_STAFF)

        self.assertTrue(staff)
        self.assertTrue(staff <= admin)
        self.assertIn("fees.waive_fee", admin)
        self.assertNotIn("fees.waive_fee", staff)

    def test_students_cannot_reach_the_money_moving_permissions(self):
        student = resolve_permissions(Roles.STUDENT)

        for forbidden in (
            "fees.waive_fee",
            "fees.add_fee",
            "fees.generate_fees",
            "payments.add_payment",
            "payments.cancel_payment",
            "payment_submissions.review_paymentsubmission",
            "audits.view_all_audit_logs",
            "rbac.view_admin_dashboard",
            "rbac.manage_roles",
        ):
            self.assertNotIn(forbidden, student, forbidden)

    def test_assigning_a_role_grants_its_permissions_immediately(self):
        user = User.objects.create_user(username="desk", password="pw")
        self.assertFalse(user.has_perm("fees.view_fee"))

        assign_role(user, Roles.CMS_STAFF)

        self.assertTrue(user.has_perm("fees.view_fee"))
        self.assertTrue(user.is_staff)


class ActionPermissionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="u", password="pw")

    def _check(self, view, table, action, method="get"):
        view.required_permissions = table
        view.action = action
        request = getattr(self.factory, method)("/")
        request.user = self.user
        return ActionPermission().has_permission(request, view)

    def test_an_action_with_no_entry_is_denied(self):
        view = APIView()
        self.assertFalse(self._check(view, {"list": ALLOW_ANY}, "explode"))

    def test_the_default_entry_catches_unlisted_actions(self):
        view = APIView()
        self.assertTrue(self._check(view, {DEFAULT: ALLOW_ANY}, "explode"))

    def test_deny_beats_a_default(self):
        view = APIView()
        table = {"destroy": DENY, DEFAULT: ALLOW_ANY}
        self.assertFalse(self._check(view, table, "destroy"))

    def test_a_list_requires_every_permission(self):
        assign_role(self.user, Roles.STUDENT)
        view = APIView()

        table = {"go": ["fees.view_fee", "fees.waive_fee"]}
        self.assertFalse(self._check(view, table, "go"))

        table = {"go": ["fees.view_fee", "payments.view_payment"]}
        self.assertTrue(self._check(view, table, "go"))

    def test_any_of_needs_only_one(self):
        assign_role(self.user, Roles.STUDENT)
        view = APIView()

        table = {"go": AnyOf("fees.waive_fee", "fees.view_fee")}
        self.assertTrue(self._check(view, table, "go"))

        table = {"go": AnyOf("fees.waive_fee", "fees.generate_fees")}
        self.assertFalse(self._check(view, table, "go"))

    def test_a_view_without_a_table_is_a_programming_error(self):
        view = APIView()
        view.action = "list"
        request = self.factory.get("/")
        request.user = self.user

        with self.assertRaises(AssertionError):
            ActionPermission().has_permission(request, view)

    def test_plain_apiviews_key_off_the_http_method(self):
        view = APIView()
        view.required_permissions = {"post": ALLOW_ANY}
        request = self.factory.post("/")
        request.user = self.user

        self.assertTrue(ActionPermission().has_permission(request, view))

        request = self.factory.get("/")
        request.user = self.user
        self.assertFalse(ActionPermission().has_permission(request, view))


class CrudHelperTests(TestCase):
    def test_it_builds_the_five_crud_entries_plus_a_default(self):
        table = crud("fees", "fee")

        self.assertEqual(table["list"], "fees.view_fee")
        self.assertEqual(table["create"], "fees.add_fee")
        self.assertEqual(table["update"], "fees.change_fee")
        self.assertEqual(table["partial_update"], "fees.change_fee")
        self.assertEqual(table["destroy"], "fees.delete_fee")
        self.assertEqual(table[DEFAULT], "fees.view_fee")

    def test_extras_are_added_and_can_override(self):
        table = crud("fees", "fee", waive="fees.waive_fee", default=DENY)

        self.assertEqual(table["waive"], "fees.waive_fee")
        self.assertIs(table[DEFAULT], DENY)


class AccessEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school = School.objects.create(school_name="QA Scope School", short_name="QA")
        self.institute = Institute.objects.create(institute_name="QA Institute", school=self.school)

    def test_me_reports_the_callers_roles_and_permissions(self):
        user = User.objects.create_user(username="desk", password="pw")
        assign_role(user, Roles.CMS_STAFF)
        self.client.force_authenticate(user)

        response = self.client.get("/api/v1/access/me/")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["roles"], [Roles.CMS_STAFF])
        self.assertIn("fees.view_fee", data["permissions"])
        self.assertNotIn("fees.waive_fee", data["permissions"])

    def test_the_catalog_needs_the_catalog_permission(self):
        user = User.objects.create_user(username="nobody", password="pw")
        self.client.force_authenticate(user)

        self.assertEqual(self.client.get("/api/v1/access/roles/").status_code, 403)

        assign_role(user, Roles.CMS_ADMIN)
        self.assertEqual(self.client.get("/api/v1/access/roles/").status_code, 200)

    def test_assigning_roles_needs_manage_roles(self):
        actor = User.objects.create_user(username="actor", password="pw", institute=self.institute)
        assign_role(actor, Roles.CMS_ADMIN)
        target = User.objects.create_user(username="target", password="pw", institute=self.institute)
        self.client.force_authenticate(actor)

        payload = {"user_id": target.pk, "roles": [Roles.CMS_STAFF]}
        self.assertEqual(
            self.client.post("/api/v1/access/assign/", payload, format="json").status_code,
            403,
        )

        assign_role(actor, Roles.SCHOOL_ADMIN)
        response = self.client.post(
            "/api/v1/access/assign/", payload, format="json"
        )

        self.assertEqual(response.status_code, 200)
        target.refresh_from_db()
        self.assertEqual(
            list(target.groups.values_list("name", flat=True)), [Roles.CMS_STAFF]
        )
        self.assertTrue(target.is_staff)

    def test_an_unknown_role_is_rejected(self):
        actor = User.objects.create_user(username="actor", password="pw", institute=self.institute)
        assign_role(actor, Roles.SCHOOL_ADMIN)
        target = User.objects.create_user(username="target", password="pw", institute=self.institute)
        self.client.force_authenticate(actor)

        response = self.client.post(
            "/api/v1/access/assign/",
            {"user_id": target.pk, "roles": ["Wizards"]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)


class PermissionCodenameTests(TestCase):
    def test_superusers_report_the_whole_catalog(self):
        superuser = User.objects.create_superuser(
            username="root", password="pw", email="root@example.com"
        )
        codenames = user_permission_codenames(superuser)

        self.assertIn("fees.waive_fee", codenames)
        self.assertIn("rbac.manage_roles", codenames)

    def test_a_user_in_no_group_holds_nothing(self):
        user = User.objects.create_user(username="nobody", password="pw")
        self.assertEqual(user_permission_codenames(user), [])

    def test_group_permissions_show_up(self):
        user = User.objects.create_user(username="student", password="pw")
        assign_role(user, Roles.STUDENT)

        codenames = user_permission_codenames(user)
        self.assertIn("fees.view_fee", codenames)
        self.assertIn("payment_submissions.add_paymentsubmission", codenames)

    def test_an_empty_group_grants_nothing(self):
        user = User.objects.create_user(username="ghost", password="pw")
        user.groups.add(Group.objects.create(name="Empty"))

        self.assertEqual(user_permission_codenames(user), [])


class FeatureFlagTests(TestCase):
    """Switching a module off must close every door, not just hide the link."""

    ALL_ON = {
        "contributions": True,
        "contribution_points": True,
        "complaints": True,
    }

    def setUp(self):
        self.user = User.objects.create_user(username="flagged", password="pw")
        assign_role(self.user, Roles.STUDENT)

    def test_a_child_feature_cannot_outlive_its_parent(self):
        with override_settings(
            FEATURES={**self.ALL_ON, "contributions": False}
        ):
            self.assertFalse(features.is_enabled(features.CONTRIBUTIONS))
            # Points were left True in the env, and are still off.
            self.assertFalse(features.is_enabled(features.CONTRIBUTION_POINTS))

    def test_an_unknown_flag_is_off(self):
        with override_settings(FEATURES=self.ALL_ON):
            self.assertFalse(features.is_enabled("teleportation"))

    def test_roles_stop_granting_a_disabled_feature(self):
        with override_settings(
            FEATURES={**self.ALL_ON, "contributions": False}
        ):
            granted = resolve_permissions(Roles.STUDENT)

        self.assertNotIn("contributions.add_contribution", granted)
        self.assertNotIn("contributions.view_contribution_leaderboard", granted)
        # Everything outside the feature is untouched.
        self.assertIn("fees.view_fee", granted)

    def test_only_the_points_half_goes_when_points_are_off(self):
        with override_settings(
            FEATURES={**self.ALL_ON, "contribution_points": False}
        ):
            granted = resolve_permissions(Roles.STUDENT)

        self.assertIn("contributions.add_contribution", granted)
        self.assertNotIn("contributions.add_feediscount", granted)
        self.assertNotIn("contributions.view_contribution_leaderboard", granted)
        self.assertNotIn("fees.view_fee_discount_history", granted)

    def test_me_drops_grants_a_previous_seed_left_behind(self):
        """The database still holds the row; the payload must not report it."""
        self.assertIn(
            "contributions.add_contribution", user_permission_codenames(self.user)
        )

        with override_settings(
            FEATURES={**self.ALL_ON, "contributions": False}
        ):
            codenames = user_permission_codenames(self.user)

        self.assertNotIn("contributions.add_contribution", codenames)
        self.assertIn("fees.view_fee", codenames)

    def test_superusers_do_not_see_a_disabled_feature_either(self):
        superuser = User.objects.create_superuser(
            username="root2", password="pw", email="root2@example.com"
        )
        with override_settings(
            FEATURES={**self.ALL_ON, "contributions": False}
        ):
            codenames = user_permission_codenames(superuser)

        self.assertNotIn("contributions.review_contribution", codenames)
        self.assertIn("fees.waive_fee", codenames)

    def test_a_disabled_feature_refuses_the_action_it_gates(self):
        view = APIView()
        view.required_permissions = {
            "list": Feature(features.CONTRIBUTION_POINTS, ALLOW_ANY)
        }
        view.action = "list"
        request = RequestFactory().get("/")
        request.user = self.user

        with override_settings(
            FEATURES={**self.ALL_ON, "contribution_points": False}
        ):
            self.assertFalse(ActionPermission().has_permission(request, view))

        with override_settings(FEATURES=self.ALL_ON):
            self.assertTrue(ActionPermission().has_permission(request, view))

    def test_gate_leaves_a_narrower_wrapper_alone(self):
        table = gate(
            "contributions",
            {"list": "contributions.view_contribution", "quote": Feature("x", DENY)},
        )
        self.assertEqual(table["list"].feature, "contributions")
        self.assertEqual(table["quote"].feature, "x")
