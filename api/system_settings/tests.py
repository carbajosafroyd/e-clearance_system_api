"""
Feature flags stored per institute, so a module can be switched on or off
without a deploy.

The environment still decides what the build ships. These cover the layer
underneath it: the row an institute writes, who may write it, and what
happens to everything downstream when they do.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.institutes.models import Institute
from api.schools.models import School
from api.system_settings.models import SystemSetting
from core import features

User = get_user_model()

ALL_ON = {
    features.CONTRIBUTIONS: True,
    features.CONTRIBUTION_POINTS: True,
    features.COMPLAINTS: True,
    features.COUPONS: True,
}


def make_institute(name: str, acronym: str) -> Institute:
    school, _ = School.objects.get_or_create(
        school_name="Test School", defaults={"short_name": "TS"}
    )
    return Institute.objects.create(
        institute_name=name, acronym=acronym, school=school
    )


@override_settings(FEATURES=ALL_ON)
class InstituteFeatureResolutionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.computing = make_institute("Institute of Computing", "IC")
        self.teaching = make_institute("Institute of Teacher Education", "ITE")

    def set_flag(self, institute, feature, value):
        SystemSetting.objects.update_or_create(
            institute=institute,
            setting_key=features.setting_key(feature),
            defaults={"setting_value": str(value).lower()},
        )

    def test_an_institute_with_no_row_inherits_the_build_default(self):
        """The first deploy of this must change nothing for anybody."""
        self.assertTrue(features.is_enabled(features.COUPONS, self.teaching))

    def test_a_row_turns_a_module_off_for_that_institute_only(self):
        self.set_flag(self.teaching, features.COUPONS, False)

        self.assertFalse(features.is_enabled(features.COUPONS, self.teaching))
        self.assertTrue(features.is_enabled(features.COUPONS, self.computing))

    @override_settings(FEATURES={**ALL_ON, features.COUPONS: False})
    def test_a_row_cannot_switch_on_what_the_build_does_not_ship(self):
        """
        The environment is a kill switch, not a default to be argued with.

        Its routes are not registered, so a row saying otherwise would
        advertise a module whose endpoints 404.
        """
        self.set_flag(self.computing, features.COUPONS, True)

        self.assertFalse(features.is_enabled(features.COUPONS, self.computing))

    def test_switching_the_parent_off_takes_the_child_with_it(self):
        self.set_flag(self.computing, features.CONTRIBUTIONS, False)
        self.set_flag(self.computing, features.CONTRIBUTION_POINTS, True)

        self.assertFalse(
            features.is_enabled(features.CONTRIBUTION_POINTS, self.computing)
        )

    def test_a_stored_value_is_read_the_way_people_type_it(self):
        for written, expected in [
            ("TRUE", True),
            ("Yes", True),
            ("1", True),
            ("false", False),
            ("off", False),
            ("0", False),
        ]:
            self.set_flag(self.computing, features.COUPONS, written)
            cache.clear()
            self.assertIs(
                features.is_enabled(features.COUPONS, self.computing),
                expected,
                f"{written!r} should read as {expected}",
            )

    def test_nonsense_in_the_column_does_not_read_as_true(self):
        """`bool("false")` is True, which is exactly the trap here.

        An unparseable value fails closed, the same way an unknown feature
        name does: a typo showing up as a missing page is recoverable, a typo
        leaving an endpoint open is not.
        """
        SystemSetting.objects.create(
            institute=self.computing,
            setting_key=features.setting_key(features.COUPONS),
            setting_value="banana",
        )

        self.assertFalse(features.is_enabled(features.COUPONS, self.computing))

    def test_saving_a_row_takes_effect_immediately(self):
        """A cached flag that outlives the save is a toggle that lies."""
        self.assertTrue(features.is_enabled(features.COUPONS, self.computing))

        self.set_flag(self.computing, features.COUPONS, False)

        self.assertFalse(features.is_enabled(features.COUPONS, self.computing))

    def test_with_no_institute_the_build_answers_alone(self):
        """What `seed_rbac` and anything outside a request should get."""
        self.set_flag(self.computing, features.COUPONS, False)

        self.assertTrue(features.is_enabled(features.COUPONS))


@override_settings(FEATURES=ALL_ON)
class ContributionsAreForComputingTests(TestCase):
    """
    The contribution scheme belongs to the institute that built it.

    Restricted on the acronym rather than the display name: a name is edited
    for presentation, and policy keyed to a substring of one breaks the day
    somebody fixes a typo in it.
    """

    def setUp(self):
        cache.clear()
        self.computing = make_institute("Institute of Computing", "IC")
        self.teaching = make_institute("Institute of Teacher Education", "ITE")

    def test_computing_may_run_it(self):
        self.assertTrue(
            features.is_available(features.CONTRIBUTIONS, self.computing)
        )
        self.assertTrue(features.is_enabled(features.CONTRIBUTIONS, self.computing))

    def test_another_institute_may_not(self):
        self.assertFalse(
            features.is_available(features.CONTRIBUTIONS, self.teaching)
        )
        self.assertFalse(features.is_enabled(features.CONTRIBUTIONS, self.teaching))

    def test_a_row_does_not_let_another_institute_in(self):
        """Availability is not something a settings write can grant itself."""
        SystemSetting.objects.create(
            institute=self.teaching,
            setting_key=features.setting_key(features.CONTRIBUTIONS),
            setting_value="true",
        )

        self.assertFalse(features.is_enabled(features.CONTRIBUTIONS, self.teaching))

    def test_the_points_half_is_restricted_with_it(self):
        self.assertFalse(
            features.is_available(features.CONTRIBUTION_POINTS, self.teaching)
        )

    def test_an_unrestricted_module_is_available_to_everyone(self):
        self.assertTrue(features.is_available(features.COUPONS, self.teaching))
        self.assertTrue(features.is_available(features.COMPLAINTS, self.teaching))

    def test_the_toggle_list_says_unavailable_rather_than_omitting_it(self):
        """A missing switch reads as a bug; a disabled one reads as a decision."""
        rows = {
            row["feature"]: row
            for row in features.feature_settings(self.teaching)
        }

        self.assertIn(features.CONTRIBUTIONS, rows)
        self.assertFalse(rows[features.CONTRIBUTIONS]["available"])
        self.assertFalse(rows[features.CONTRIBUTIONS]["enabled"])
        self.assertTrue(rows[features.COUPONS]["available"])
        self.assertEqual(
            rows[features.CONTRIBUTION_POINTS]["requires"], features.CONTRIBUTIONS
        )


@override_settings(FEATURES=ALL_ON)
class SettingsEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.computing = make_institute("Institute of Computing", "IC")
        self.teaching = make_institute("Institute of Teacher Education", "ITE")

        self.admin = User.objects.create_user(
            username="settings-admin", password="pw", institute=self.computing
        )
        self.admin.is_superuser = True
        self.admin.save()

        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def bulk(self, payload):
        return self.client.patch(
            "/api/v1/system-settings/bulk-update/",
            {"settings": payload},
            format="json",
        )

    def test_a_toggle_is_saved_and_read_back(self):
        response = self.bulk({features.setting_key(features.COUPONS): "false"})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(features.is_enabled(features.COUPONS, self.computing))

    def test_switching_the_parent_off_writes_the_child_off_too(self):
        """
        Stored consistently, not merely resolved consistently.

        A row saying points are on while every check reports them off is an
        afternoon of somebody's life.
        """
        self.bulk(
            {
                features.setting_key(features.CONTRIBUTIONS): "false",
                features.setting_key(features.CONTRIBUTION_POINTS): "true",
            }
        )

        stored = SystemSetting.objects.get(
            institute=self.computing,
            setting_key=features.setting_key(features.CONTRIBUTION_POINTS),
        )
        self.assertEqual(stored.setting_value, "false")

    def test_an_institute_cannot_switch_on_what_it_may_not_run(self):
        self.admin.institute = self.teaching
        self.admin.save()

        response = self.bulk(
            {features.setting_key(features.CONTRIBUTIONS): "true"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"]["code"], "feature_not_available")

    def test_the_refusal_is_explicit_rather_than_a_silent_drop(self):
        """A switch that flips back on save with no message is unexplainable."""
        self.admin.institute = self.teaching
        self.admin.save()

        response = self.bulk(
            {features.setting_key(features.CONTRIBUTIONS): "true"}
        )

        self.assertFalse(
            SystemSetting.objects.filter(
                institute=self.teaching,
                setting_key=features.setting_key(features.CONTRIBUTIONS),
            ).exists()
        )
        self.assertIn("cannot switch", response.json()["errors"]["detail"])

    def test_turning_one_off_is_allowed_even_where_it_is_unavailable(self):
        """Off is always a legal thing to ask for."""
        self.admin.institute = self.teaching
        self.admin.save()

        response = self.bulk(
            {features.setting_key(features.CONTRIBUTIONS): "false"}
        )

        self.assertEqual(response.status_code, 200)

    def test_the_toggles_endpoint_describes_each_switch(self):
        response = self.client.get("/api/v1/system-settings/features/")

        self.assertEqual(response.status_code, 200)
        rows = {row["feature"]: row for row in response.json()["data"]}
        self.assertEqual(set(rows), set(features.ALL_FEATURES))
        self.assertTrue(rows[features.COUPONS]["available"])
        self.assertEqual(
            rows[features.COUPONS]["key"], features.setting_key(features.COUPONS)
        )

    def test_a_non_feature_setting_still_saves_normally(self):
        response = self.bulk({"current_school_year": "2030-2031"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            SystemSetting.objects.filter(
                institute=self.computing,
                setting_key="current_school_year",
                setting_value="2030-2031",
            ).exists()
        )


@override_settings(FEATURES=ALL_ON)
class FlagsReachTheApiTests(TestCase):
    """A stored toggle has to close the endpoints, not just hide the buttons."""

    def setUp(self):
        cache.clear()
        self.institute = make_institute("Institute of Computing", "IC")
        self.user = User.objects.create_user(
            username="coupon-staff", password="pw", institute=self.institute
        )
        self.user.is_superuser = True
        self.user.save()

        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def set_flag(self, feature, value):
        SystemSetting.objects.update_or_create(
            institute=self.institute,
            setting_key=features.setting_key(feature),
            defaults={"setting_value": str(value).lower()},
        )

    def test_me_reports_the_institutes_own_flags(self):
        self.set_flag(features.COUPONS, False)

        payload = self.client.get("/api/v1/access/me/").json()["data"]

        self.assertFalse(payload["features"][features.COUPONS])
        self.assertTrue(payload["features"][features.COMPLAINTS])

    def test_an_endpoint_refuses_once_its_module_is_switched_off(self):
        self.assertEqual(
            self.client.get("/api/v1/coupon-batches/").status_code, 200
        )

        self.set_flag(features.COUPONS, False)

        self.assertEqual(
            self.client.get("/api/v1/coupon-batches/").status_code, 403
        )

    def test_the_permissions_list_drops_what_a_disabled_module_owns(self):
        self.set_flag(features.COUPONS, False)

        payload = self.client.get("/api/v1/access/me/").json()["data"]

        self.assertFalse(
            [p for p in payload["permissions"] if p.startswith("coupons.")]
        )

    def test_a_blank_acronym_is_not_read_as_a_refusal(self):
        """
        The field is new, so every institute predating it has a blank one.

        Reading that as "not entitled" would switch the contribution scheme
        off for the institute that built it on the day this deploys. The
        restriction starts applying once acronyms are actually filled in.
        """
        legacy = make_institute("Institute Without An Acronym", "")

        self.assertTrue(features.is_available(features.CONTRIBUTIONS, legacy))
        self.assertTrue(features.is_enabled(features.CONTRIBUTIONS, legacy))

    def test_filling_the_acronym_in_is_what_applies_the_rule(self):
        legacy = make_institute("Institute Of Something Else", "")
        self.assertTrue(features.is_available(features.CONTRIBUTIONS, legacy))

        legacy.acronym = "ISE"
        legacy.save()

        self.assertFalse(features.is_available(features.CONTRIBUTIONS, legacy))


@override_settings(FEATURES=ALL_ON)
class FeatureSeedingTests(TestCase):
    """
    `seed_system_settings` writes the rows the settings page toggles.

    Seeded at the environment's own default, so the first run records what
    was already true rather than changing anything.
    """

    def setUp(self):
        cache.clear()
        self.computing = make_institute("Institute of Computing", "IC")
        self.teaching = make_institute("Institute of Teacher Education", "ITE")

    def seed(self):
        from django.core.management import call_command
        from io import StringIO

        call_command("seed_system_settings", stdout=StringIO(), stderr=StringIO())

    def rows(self, institute):
        return dict(
            SystemSetting.objects.filter(
                institute=institute, setting_key__startswith=features.SETTING_PREFIX
            ).values_list("setting_key", "setting_value")
        )

    def test_a_row_is_written_for_every_module_the_institute_may_run(self):
        self.seed()

        self.assertEqual(
            set(self.rows(self.computing)),
            {features.setting_key(f) for f in features.ALL_FEATURES},
        )

    def test_no_row_is_written_for_a_module_the_institute_may_not_run(self):
        """A dead switch on a settings page is worse than no switch."""
        self.seed()

        keys = set(self.rows(self.teaching))
        self.assertNotIn(features.setting_key(features.CONTRIBUTIONS), keys)
        self.assertNotIn(features.setting_key(features.CONTRIBUTION_POINTS), keys)
        self.assertIn(features.setting_key(features.COUPONS), keys)

    @override_settings(FEATURES={**ALL_ON, features.COUPONS: False})
    def test_a_module_the_build_does_not_ship_gets_no_row(self):
        """
        Nothing to toggle, so nothing to write.

        Its routes are not registered either, so a row would be a switch
        whose only effect is to advertise endpoints that 404.
        """
        self.seed()

        self.assertNotIn(
            features.setting_key(features.COUPONS), self.rows(self.computing)
        )

    def test_the_seeded_value_matches_what_the_build_ships(self):
        """The first run records what was already true, changing nothing."""
        self.seed()

        self.assertEqual(
            self.rows(self.computing)[features.setting_key(features.COUPONS)],
            "true",
        )

    def test_seeding_twice_does_not_overwrite_a_deliberate_choice(self):
        """Re-running the seeders must not undo somebody's settings."""
        self.seed()
        SystemSetting.objects.filter(
            institute=self.computing,
            setting_key=features.setting_key(features.COUPONS),
        ).update(setting_value="false")

        self.seed()

        self.assertEqual(
            self.rows(self.computing)[features.setting_key(features.COUPONS)],
            "false",
        )
