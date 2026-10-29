"""
Which settings a caller can see, and whose.

Two questions, deliberately separate.

**Which system?** A row carries the system it configures, so the collection
app can ask for the collection settings instead of pulling every row on the
platform and sorting them out on the client. The platform rows come back
alongside, because the academic year is not the collection app's setting - it
is the institute's, and every system filters by it.

**Whose institute?** Everybody is scoped to their own. A school administrator
answers for the institutes under them, so `manage_school_settings` widens the
reach to their school and stops there - the interesting assertion in here is
the one about the *other* school.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.institutes.models import Institute
from api.schools.models import School
from api.system_settings.models import SystemSetting
from core import features, setting_buckets
from core.choices import SystemScope

User = get_user_model()

ALL_ON = {
    features.CONTRIBUTIONS: True,
    features.CONTRIBUTION_POINTS: True,
    features.COMPLAINTS: True,
    features.COUPONS: True,
}

LIST_URL = "/api/v1/system-settings/"


@override_settings(FEATURES=ALL_ON)
class ScopeTestCase(TestCase):
    def setUp(self):
        cache.clear()

        self.school = School.objects.create(
            school_name="Scope School", short_name="SS"
        )
        self.other_school = School.objects.create(
            school_name="Other School", short_name="OS"
        )

        self.computing = Institute.objects.create(
            institute_name="Institute of Computing", acronym="IC", school=self.school
        )
        self.sibling = Institute.objects.create(
            institute_name="Institute of Education", acronym="IE", school=self.school
        )
        self.outsider = Institute.objects.create(
            institute_name="Institute of Elsewhere",
            acronym="IX",
            school=self.other_school,
        )

        self.client = APIClient()

    def make_user(self, username, institute, *codenames, school=None):
        user = User.objects.create_user(
            username=username, password="pw", institute=institute, school=school
        )
        for codename in codenames:
            app_label, name = codename.split(".", 1)
            user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label=app_label, codename=name
                )
            )
        return User.objects.get(pk=user.pk)

    def setting(self, institute, key, value="false"):
        return SystemSetting.objects.create(
            institute=institute,
            setting_key=key,
            setting_value=value,
            system=setting_buckets.system_for(key),
        )

    def rows(self, response):
        """The row list, whether or not the endpoint is paginated.

        `CustomResponseRenderer` wraps everything in `data`; pagination adds a
        `results` inside it. Reading both means these tests keep passing the
        day somebody puts a paginator on this viewset.
        """
        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        return payload["results"] if isinstance(payload, dict) else payload

    def keys(self, response):
        return {row["setting_key"] for row in self.rows(response)}


class SystemFilterTests(ScopeTestCase):
    def setUp(self):
        super().setUp()
        self.setting(self.computing, "current_school_year", "2025-2026")
        self.setting(self.computing, "collection_student_maintenance_mode")
        self.setting(self.computing, "locker_student_maintenance_mode")
        self.setting(self.computing, "feature_coupons", "true")

        self.client.force_authenticate(
            self.make_user(
                "reader", self.computing, "system_settings.view_systemsetting"
            )
        )

    def test_asking_for_one_system_leaves_the_others_out(self):
        keys = self.keys(self.client.get(LIST_URL, {"system": "collection"}))

        self.assertIn("collection_student_maintenance_mode", keys)
        self.assertNotIn("locker_student_maintenance_mode", keys)

    def test_the_platform_rows_come_back_with_every_system(self):
        """The term is the institute's, not any one system's.

        Leaving it out would mean the collection app needs a second request
        for the two rows it cannot filter a single list without.
        """
        keys = self.keys(self.client.get(LIST_URL, {"system": "collection"}))

        self.assertIn("current_school_year", keys)

    def test_the_optional_modules_are_collection_settings(self):
        keys = self.keys(self.client.get(LIST_URL, {"system": "collection"}))

        self.assertIn("feature_coupons", keys)

    def test_asking_for_nothing_still_returns_everything(self):
        keys = self.keys(self.client.get(LIST_URL))

        self.assertIn("locker_student_maintenance_mode", keys)

    def test_an_unknown_system_is_refused_rather_than_ignored(self):
        """Silently returning everything would look like the filter worked."""
        response = self.client.get(LIST_URL, {"system": "kitchen_sink"})

        self.assertEqual(response.status_code, 400)

    def test_a_row_is_stamped_with_the_system_it_configures(self):
        row = SystemSetting.objects.get(
            setting_key="collection_student_maintenance_mode"
        )
        self.assertEqual(row.system, SystemScope.COLLECTION)

        self.assertEqual(
            SystemSetting.objects.get(setting_key="current_school_year").system,
            SystemScope.PLATFORM,
        )


class InstituteScopeTests(ScopeTestCase):
    def setUp(self):
        super().setUp()
        for institute in (self.computing, self.sibling, self.outsider):
            self.setting(institute, "current_school_year", "2025-2026")
            self.setting(institute, "collection_student_maintenance_mode")

    def bulk(self, payload, institute=None):
        body = {"settings": payload}
        if institute is not None:
            body["institute"] = institute.pk
        return self.client.patch(
            "/api/v1/system-settings/bulk-update/", body, format="json"
        )

    def institutes(self, response):
        return {row["institute"] for row in self.rows(response)}

    # -- reading ---------------------------------------------------------

    def test_an_institute_admin_sees_only_their_own_institute(self):
        self.client.force_authenticate(
            self.make_user(
                "ia", self.computing, "system_settings.view_systemsetting"
            )
        )

        self.assertEqual(
            self.institutes(self.client.get(LIST_URL)), {self.computing.pk}
        )

    def test_a_school_admin_sees_every_institute_in_their_school(self):
        self.client.force_authenticate(
            self.make_user(
                "sa",
                self.computing,
                "system_settings.view_systemsetting",
                "system_settings.manage_school_settings",
                school=self.school,
            )
        )

        self.assertEqual(
            self.institutes(self.client.get(LIST_URL)),
            {self.computing.pk, self.sibling.pk},
        )

    def test_a_school_admin_never_sees_another_school(self):
        """The assertion the whole widening exists to bound."""
        self.client.force_authenticate(
            self.make_user(
                "sa-bounded",
                self.computing,
                "system_settings.view_systemsetting",
                "system_settings.manage_school_settings",
                school=self.school,
            )
        )

        self.assertNotIn(self.outsider.pk, self.institutes(self.client.get(LIST_URL)))

    def test_a_school_admin_attached_through_an_institute_reaches_its_school(self):
        """
        `user.school` empty, `user.institute` set: the institute says which
        school, and that is the school they reach - every institute of it,
        never another school's. An account attached to neither reaches
        nothing (the test below).
        """
        self.client.force_authenticate(
            self.make_user(
                "sa-schoolless",
                self.computing,
                "system_settings.view_systemsetting",
                "system_settings.manage_school_settings",
            )
        )

        self.assertEqual(
            self.institutes(self.client.get(LIST_URL)),
            {self.computing.pk, self.sibling.pk},
        )

    def test_a_school_admin_attached_to_nothing_reaches_nothing(self):
        """Missing information is not a reason to widen the blast radius."""
        self.client.force_authenticate(
            self.make_user(
                "sa-unplaced",
                None,
                "system_settings.view_systemsetting",
                "system_settings.manage_school_settings",
            )
        )

        self.assertEqual(self.institutes(self.client.get(LIST_URL)), set())

    # -- writing ---------------------------------------------------------

    def test_a_school_admin_may_close_a_sibling_institute(self):
        self.client.force_authenticate(
            self.make_user(
                "sa-write",
                self.computing,
                "system_settings.view_systemsetting",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_maintenance",
                "system_settings.manage_school_settings",
                school=self.school,
            )
        )

        response = self.bulk(
            {"collection_officer_maintenance_mode": "true"}, institute=self.sibling
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SystemSetting.objects.get(
                institute=self.sibling,
                setting_key="collection_officer_maintenance_mode",
            ).setting_value,
            "true",
        )

    def test_a_school_admin_may_not_close_another_school(self):
        self.client.force_authenticate(
            self.make_user(
                "sa-blocked",
                self.computing,
                "system_settings.view_systemsetting",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_maintenance",
                "system_settings.manage_school_settings",
                school=self.school,
            )
        )

        response = self.bulk(
            {"collection_officer_maintenance_mode": "true"}, institute=self.outsider
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            SystemSetting.objects.filter(
                institute=self.outsider,
                setting_key="collection_officer_maintenance_mode",
            ).exists()
        )

    def test_naming_an_institute_you_cannot_reach_is_refused(self):
        """Without the widening, `institute` must not be a way around it."""
        self.client.force_authenticate(
            self.make_user(
                "ia-write",
                self.computing,
                "system_settings.view_systemsetting",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_student_maintenance",
            )
        )

        response = self.bulk(
            {"collection_student_maintenance_mode": "true"}, institute=self.sibling
        )

        self.assertEqual(response.status_code, 403)

    def test_a_write_with_no_institute_lands_on_your_own(self):
        self.client.force_authenticate(
            self.make_user(
                "ia-default",
                self.computing,
                "system_settings.view_systemsetting",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_student_maintenance",
            )
        )

        self.assertEqual(
            self.bulk({"collection_student_maintenance_mode": "true"}).status_code,
            200,
        )
        self.assertEqual(
            SystemSetting.objects.get(
                institute=self.computing,
                setting_key="collection_student_maintenance_mode",
            ).setting_value,
            "true",
        )

    def test_the_system_column_cannot_be_set_by_the_caller(self):
        """It decides which frontend is handed the row.

        A caller who could set it could file the collection shutter under
        `voting`, and the collection app would stop being told it was closed.
        """
        user = self.make_user(
            "ia-sneaky",
            self.computing,
            "system_settings.view_systemsetting",
            "system_settings.add_systemsetting",
            "system_settings.change_student_maintenance",
        )
        self.client.force_authenticate(user)

        # A key setUp has not already created, or the unique constraint
        # rejects the POST before the interesting part runs.
        response = self.client.post(
            LIST_URL,
            {
                "setting_key": "collection_student_maintenance_message",
                "setting_value": "Back at 3:00 PM",
                "system": SystemScope.VOTING,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            SystemSetting.objects.get(
                institute=self.computing,
                setting_key="collection_student_maintenance_message",
            ).system,
            SystemScope.COLLECTION,
        )


class DeskStaffTests(ScopeTestCase):
    """Officers read the settings; they never write one.

    The collection app fetches these on boot, so desk staff must be able to
    read them or they cannot use the app at all. Writing is the institute
    administrator's, and this pins that the read grant does not carry it.
    """

    def setUp(self):
        super().setUp()
        self.setting(self.computing, "collection_student_maintenance_mode")
        self.client.force_authenticate(
            self.make_user(
                "officer", self.computing, "system_settings.view_systemsetting"
            )
        )

    def test_an_officer_can_read_the_settings(self):
        self.assertEqual(self.client.get(LIST_URL).status_code, 200)

    def test_an_officer_cannot_bulk_update(self):
        response = self.client.patch(
            "/api/v1/system-settings/bulk-update/",
            {"settings": {"collection_student_maintenance_mode": "true"}},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_an_officer_cannot_patch_a_row_by_id(self):
        row = SystemSetting.objects.get(
            setting_key="collection_student_maintenance_mode"
        )

        response = self.client.patch(
            f"{LIST_URL}{row.pk}/", {"setting_value": "true"}, format="json"
        )

        self.assertEqual(response.status_code, 403)

    def test_an_officer_is_offered_no_editable_sections(self):
        response = self.client.get(f"{LIST_URL}buckets/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            any(row["can_change"] for row in response.json()["data"]),
            "a settings page would render editable controls for an officer",
        )
