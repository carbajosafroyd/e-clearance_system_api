"""
Who may change which setting.

One permission used to guard the whole key-value store, so whoever could set
the academic year could also close the institute. These pin the split: the
institute runs its own term and modules, the school is what closes it.
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

User = get_user_model()

ALL_ON = {
    features.CONTRIBUTIONS: True,
    features.CONTRIBUTION_POINTS: True,
    features.COMPLAINTS: True,
    features.COUPONS: True,
}


@override_settings(FEATURES=ALL_ON)
class SettingBucketTests(TestCase):
    def setUp(self):
        cache.clear()
        school, _ = School.objects.get_or_create(
            school_name="Bucket School", defaults={"short_name": "BS"}
        )
        self.computing = Institute.objects.create(
            institute_name="Institute of Computing", acronym="IC", school=school
        )
        self.client = APIClient()

    # -- helpers ---------------------------------------------------------

    def make_user(self, username, *codenames):
        user = User.objects.create_user(
            username=username, password="pw", institute=self.computing
        )
        for codename in codenames:
            app_label, name = codename.split(".", 1)
            user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label=app_label, codename=name
                )
            )
        # Re-read: Django caches the permission set on the instance.
        return User.objects.get(pk=user.pk)

    def bulk(self, payload):
        return self.client.patch(
            "/api/v1/system-settings/bulk-update/",
            {"settings": payload},
            format="json",
        )

    # -- the mapping -----------------------------------------------------

    def test_each_key_lands_in_the_bucket_it_belongs_to(self):
        self.assertEqual(
            setting_buckets.permission_for("current_school_year"),
            setting_buckets.TERM,
        )
        self.assertEqual(
            setting_buckets.permission_for("collection_officer_maintenance_mode"),
            setting_buckets.MAINTENANCE,
        )
        self.assertEqual(
            setting_buckets.permission_for(features.setting_key(features.COUPONS)),
            setting_buckets.FEATURES,
        )

    def test_an_unbucketed_key_falls_back_to_the_coarse_permission(self):
        """Adding a setting must never open it to more people than yesterday."""
        self.assertEqual(
            setting_buckets.permission_for("something_invented_later"),
            setting_buckets.BULK,
        )

    # -- the institute administrator -------------------------------------

    def test_an_institute_admin_may_set_the_term(self):
        self.client.force_authenticate(
            self.make_user(
                "ia-term",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_term_settings",
            )
        )

        self.assertEqual(self.bulk({"current_school_year": "2030-2031"}).status_code, 200)

    def test_an_institute_admin_may_not_close_the_staff_side(self):
        """The whole point of the split."""
        self.client.force_authenticate(
            self.make_user(
                "ia-close",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_term_settings",
                "system_settings.change_feature_settings",
            )
        )

        response = self.bulk({"collection_officer_maintenance_mode": "true"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            SystemSetting.objects.filter(setting_key="collection_officer_maintenance_mode").exists()
        )

    def test_the_coarse_permission_alone_changes_nothing(self):
        """Or splitting the buckets would have been decoration."""
        self.client.force_authenticate(
            self.make_user("coarse", "system_settings.bulk_change_systemsetting")
        )

        self.assertEqual(
            self.bulk({"current_school_year": "2030-2031"}).status_code, 403
        )
        self.assertEqual(self.bulk({"collection_officer_maintenance_mode": "true"}).status_code, 403)

    # -- the school administrator ----------------------------------------

    def test_a_school_admin_may_close_the_institute(self):
        self.client.force_authenticate(
            self.make_user(
                "sa-close",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_maintenance",
            )
        )

        self.assertEqual(self.bulk({"collection_officer_maintenance_mode": "true"}).status_code, 200)

    # -- nothing is half-applied -----------------------------------------

    def test_a_mixed_payload_writes_neither_key(self):
        """A half-saved settings form is worse than a refused one."""
        self.client.force_authenticate(
            self.make_user(
                "mixed",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_term_settings",
            )
        )

        response = self.bulk(
            {"current_school_year": "2030-2031", "collection_officer_maintenance_mode": "true"}
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            SystemSetting.objects.filter(setting_key="current_school_year").exists()
        )

    def test_the_refusal_names_the_key(self):
        self.client.force_authenticate(
            self.make_user("named", "system_settings.bulk_change_systemsetting")
        )

        response = self.bulk({"collection_officer_maintenance_mode": "true"})

        self.assertIn("collection_officer_maintenance_mode", str(response.json()))

    # -- the single-row routes are not a way round it ---------------------

    def test_patching_a_row_by_id_is_checked_too(self):
        row = SystemSetting.objects.create(
            institute=self.computing,
            setting_key="collection_officer_maintenance_mode",
            setting_value="false",
        )
        self.client.force_authenticate(
            self.make_user(
                "by-id",
                "system_settings.view_systemsetting",
                "system_settings.change_systemsetting",
                "system_settings.change_term_settings",
            )
        )

        response = self.client.patch(
            f"/api/v1/system-settings/{row.id}/",
            {"setting_value": "true"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        row.refresh_from_db()
        self.assertEqual(row.setting_value, "false")

    def test_a_row_cannot_be_renamed_into_a_bucket_you_lack(self):
        row = SystemSetting.objects.create(
            institute=self.computing,
            setting_key="current_school_year",
            setting_value="2030-2031",
        )
        self.client.force_authenticate(
            self.make_user(
                "rename",
                "system_settings.view_systemsetting",
                "system_settings.change_systemsetting",
                "system_settings.change_term_settings",
            )
        )

        response = self.client.patch(
            f"/api/v1/system-settings/{row.id}/",
            {"setting_key": "collection_officer_maintenance_mode", "setting_value": "true"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_deleting_the_maintenance_row_is_checked_too(self):
        """Deleting it is a way of lifting maintenance."""
        row = SystemSetting.objects.create(
            institute=self.computing,
            setting_key="collection_officer_maintenance_mode",
            setting_value="true",
        )
        self.client.force_authenticate(
            self.make_user(
                "deleter",
                "system_settings.view_systemsetting",
                "system_settings.delete_systemsetting",
                "system_settings.change_term_settings",
            )
        )

        response = self.client.delete(f"/api/v1/system-settings/{row.id}/")

        self.assertEqual(response.status_code, 403)
        self.assertTrue(SystemSetting.objects.filter(pk=row.pk).exists())

    # -- reading stays open ----------------------------------------------

    def test_a_student_can_still_read_every_setting(self):
        """The app filters by the term and renders modules from these rows.

        A student who could not read them could not use the app at all, so the
        split is on writing and only on writing.
        """
        SystemSetting.objects.create(
            institute=self.computing,
            setting_key="collection_officer_maintenance_mode",
            setting_value="true",
        )
        self.client.force_authenticate(
            self.make_user("reader", "system_settings.view_systemsetting")
        )

        response = self.client.get("/api/v1/system-settings/")

        self.assertEqual(response.status_code, 200)
        keys = {row["setting_key"] for row in response.json()["data"]}
        self.assertIn("collection_officer_maintenance_mode", keys)

    def test_a_row_says_whether_the_caller_may_change_it(self):
        for key, value in (
            ("collection_officer_maintenance_mode", "false"),
            ("current_school_year", "2030-2031"),
        ):
            SystemSetting.objects.create(
                institute=self.computing, setting_key=key, setting_value=value
            )
        self.client.force_authenticate(
            self.make_user(
                "flagged",
                "system_settings.view_systemsetting",
                "system_settings.change_term_settings",
            )
        )

        rows = {
            row["setting_key"]: row
            for row in self.client.get("/api/v1/system-settings/").json()["data"]
        }

        self.assertTrue(rows["current_school_year"]["can_change"])
        self.assertFalse(rows["collection_officer_maintenance_mode"]["can_change"])

    def test_the_buckets_endpoint_answers_for_a_section_with_no_row(self):
        """A fresh institute has never been closed, so has no maintenance row.

        The page still has to decide whether to offer the control, and
        `can_change` on a row that does not exist cannot tell it.
        """
        self.client.force_authenticate(
            self.make_user(
                "sections",
                "system_settings.view_systemsetting",
                "system_settings.change_term_settings",
            )
        )

        answer = {
            row["bucket"]: row["can_change"]
            for row in self.client.get("/api/v1/system-settings/buckets/").json()[
                "data"
            ]
        }

        self.assertTrue(answer[setting_buckets.TERM])
        self.assertFalse(answer[setting_buckets.MAINTENANCE])

    # -- superusers ------------------------------------------------------

    def test_a_superuser_may_change_everything(self):
        user = self.make_user("super")
        user.is_superuser = True
        user.save()
        self.client.force_authenticate(User.objects.get(pk=user.pk))

        response = self.bulk(
            {"collection_officer_maintenance_mode": "true", "current_school_year": "2030-2031"}
        )

        self.assertEqual(response.status_code, 200)


@override_settings(FEATURES=ALL_ON)
class StudentMaintenanceBucketTests(TestCase):
    """The narrow shutter, and why it is not the wide one.

    Holding students out while fee data is corrected is the institute's own
    work. Closing the whole institute is its school's. Handing the institute
    the wide switch so it could do the narrow job would give every institute
    the power to shut itself.
    """

    def setUp(self):
        cache.clear()
        school, _ = School.objects.get_or_create(
            school_name="Shutter School", defaults={"short_name": "SS"}
        )
        self.institute = Institute.objects.create(
            institute_name="Institute of Computing", acronym="IC", school=school
        )
        self.client = APIClient()

    def make_user(self, username, *codenames):
        user = User.objects.create_user(
            username=username, password="pw", institute=self.institute
        )
        for codename in codenames:
            app_label, name = codename.split(".", 1)
            user.user_permissions.add(
                Permission.objects.get(
                    content_type__app_label=app_label, codename=name
                )
            )
        return User.objects.get(pk=user.pk)

    def bulk(self, payload):
        return self.client.patch(
            "/api/v1/system-settings/bulk-update/",
            {"settings": payload},
            format="json",
        )

    def test_online_payments_is_its_own_bucket(self):
        self.assertEqual(
            setting_buckets.permission_for(setting_buckets.ONLINE_PAYMENTS_KEY),
            setting_buckets.PAYMENTS,
        )
        self.assertIn(setting_buckets.PAYMENTS, setting_buckets.ALL_BUCKETS)

    def test_online_payments_default_on_and_read_fresh(self):
        # No row: the institute was seeded before the switch existed and was
        # accepting online payments the day before.
        self.assertTrue(setting_buckets.online_payments_enabled(self.institute))

        SystemSetting.objects.create(
            institute=self.institute,
            setting_key=setting_buckets.ONLINE_PAYMENTS_KEY,
            setting_value="false",
        )
        self.assertFalse(setting_buckets.online_payments_enabled(self.institute))

        SystemSetting.objects.filter(
            institute=self.institute, setting_key=setting_buckets.ONLINE_PAYMENTS_KEY
        ).update(setting_value="TRUE")
        self.assertTrue(setting_buckets.online_payments_enabled(self.institute))

    def test_the_payment_bucket_may_close_the_lane_and_nothing_else(self):
        self.client.force_authenticate(
            self.make_user(
                "desk",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_payment_settings",
            )
        )
        self.assertEqual(
            self.bulk({setting_buckets.ONLINE_PAYMENTS_KEY: "false"}).status_code, 200
        )
        self.assertEqual(
            self.bulk({"collection_student_maintenance_mode": "true"}).status_code, 403
        )

    def test_the_student_keys_are_their_own_bucket(self):
        self.assertEqual(
            setting_buckets.permission_for("collection_student_maintenance_mode"),
            setting_buckets.STUDENT_MAINTENANCE,
        )
        self.assertEqual(
            setting_buckets.permission_for("collection_student_maintenance_message"),
            setting_buckets.STUDENT_MAINTENANCE,
        )

    def test_an_institute_admin_may_close_the_student_side(self):
        self.client.force_authenticate(
            self.make_user(
                "ia-students",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_student_maintenance",
            )
        )

        response = self.bulk(
            {
                "collection_student_maintenance_mode": "true",
                "collection_student_maintenance_message": "Back at 3:00 PM",
            }
        )

        self.assertEqual(response.status_code, 200)

    def test_closing_the_student_side_does_not_let_them_close_the_staff_side(self):
        """The narrow permission must not imply the wide one."""
        self.client.force_authenticate(
            self.make_user(
                "narrow-only",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_student_maintenance",
            )
        )

        response = self.bulk({"collection_officer_maintenance_mode": "true"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            SystemSetting.objects.filter(setting_key="collection_officer_maintenance_mode").exists()
        )

    def test_closing_the_institute_does_not_imply_the_student_switch(self):
        """They are separate grants, so neither is a back door to the other."""
        self.client.force_authenticate(
            self.make_user(
                "wide-only",
                "system_settings.bulk_change_systemsetting",
                "system_settings.change_maintenance",
            )
        )

        self.assertEqual(self.bulk({"collection_student_maintenance_mode": "true"}).status_code, 403)

    def test_it_appears_as_its_own_section(self):
        self.client.force_authenticate(
            self.make_user(
                "sections",
                "system_settings.view_systemsetting",
                "system_settings.change_student_maintenance",
            )
        )

        answer = {
            row["bucket"]: row["can_change"]
            for row in self.client.get("/api/v1/system-settings/buckets/").json()[
                "data"
            ]
        }

        self.assertTrue(answer[setting_buckets.STUDENT_MAINTENANCE])
        self.assertFalse(answer[setting_buckets.MAINTENANCE])
