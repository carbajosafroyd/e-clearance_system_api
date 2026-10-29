"""
The settings catalog is one list, and these keep it that way.

`core.setting_buckets.CATALOG` declares each setting once - its key, the
permission that governs writing it, its starting value, its description and
the system it configures. The seeder walks the same list, narrowed to the
systems this deployment actually serves. Before they were merged, the seeder had its
own copy, and a disagreement between the two was silent in both directions: a
row seeded into every institute that nothing governed, or a permission rule
about a row nobody ever created.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

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


class CatalogShapeTests(TestCase):
    def test_every_setting_is_declared_once(self):
        keys = [spec.key for spec in setting_buckets.CATALOG]

        self.assertEqual(len(keys), len(set(keys)), "a key is declared twice")

    def test_every_setting_names_a_real_bucket(self):
        """A typo in a bucket falls through to the coarse permission.

        Which is the dangerous direction: the setting stays editable by
        everyone who used to be able to edit everything, and nothing says so.
        """
        for spec in setting_buckets.CATALOG:
            with self.subTest(setting=spec.key):
                self.assertIn(spec.bucket, setting_buckets.ALL_BUCKETS)

    def test_every_bucket_owns_at_least_one_setting(self):
        """A bucket with nothing in it is a permission nobody needs."""
        for bucket in setting_buckets.ALL_BUCKETS:
            if bucket == setting_buckets.FEATURES:
                # Feature keys are generated from `core.features`, not listed
                # in the catalog - there is one per optional module, and
                # listing them again would be the same drift elsewhere.
                continue
            with self.subTest(bucket=bucket):
                self.assertTrue(setting_buckets.keys_in(bucket))

    def test_a_catalog_key_is_never_governed_by_the_fallback(self):
        for spec in setting_buckets.CATALOG:
            with self.subTest(setting=spec.key):
                self.assertNotEqual(
                    setting_buckets.permission_for(spec.key), setting_buckets.BULK
                )

    def test_a_default_resolves_to_a_string(self):
        """Stored in a TextField, so `str()` of a callable would be seeded."""
        for spec in setting_buckets.CATALOG:
            with self.subTest(setting=spec.key):
                self.assertIsInstance(spec.initial_value(), str)

    def test_every_setting_explains_itself(self):
        """The description is what the settings page shows for an unknown key."""
        for spec in setting_buckets.CATALOG:
            with self.subTest(setting=spec.key):
                self.assertTrue(spec.description.strip())


@override_settings(FEATURES=ALL_ON, LIVE_SYSTEMS=(SystemScope.COLLECTION,))
class SeederFollowsTheCatalogTests(TestCase):
    def setUp(self):
        cache.clear()
        school = School.objects.create(school_name="Catalog School", short_name="CS")
        self.institute = Institute.objects.create(
            institute_name="Institute of Computing", acronym="IC", school=school
        )

    def seed(self):
        call_command("seed_system_settings", verbosity=0)

    def written_keys(self):
        return set(
            SystemSetting.objects.filter(institute=self.institute).values_list(
                "setting_key", flat=True
            )
        )

    def test_seeding_writes_every_key_for_a_system_we_run(self):
        self.seed()

        wanted = {spec.key for spec in setting_buckets.seeded_specs()}
        self.assertTrue(wanted)
        self.assertTrue(wanted <= self.written_keys())

    def test_seeding_skips_systems_this_deployment_does_not_run(self):
        """A shutter for an app nobody has built is a switch that does nothing.

        `core.features` already refuses to seed a module an institute is not
        entitled to, on the grounds that it would put a dead control on the
        settings page. A maintenance switch for a system with no frontend is
        the same dead control, and the person flipping it has no way to tell.
        """
        self.seed()

        running = set(setting_buckets.live_systems())
        dead = [
            spec.key
            for spec in setting_buckets.CATALOG
            if spec.system != SystemScope.PLATFORM and spec.system not in running
        ]
        self.assertTrue(dead, "every system is live; this test proves nothing")

        self.assertFalse(set(dead) & self.written_keys())

    def test_the_term_is_seeded_whatever_runs(self):
        """PLATFORM rows are not any one system's, so they are never skipped."""
        self.seed()

        written = self.written_keys()
        for spec in setting_buckets.CATALOG:
            if spec.system == SystemScope.PLATFORM:
                with self.subTest(setting=spec.key):
                    self.assertIn(spec.key, written)

    def test_every_row_is_stamped_with_the_system_it_configures(self):
        """The column a frontend filters on, so it must match the catalog."""
        self.seed()

        for row in SystemSetting.objects.filter(institute=self.institute):
            with self.subTest(setting=row.setting_key):
                self.assertEqual(
                    row.system, setting_buckets.system_for(row.setting_key)
                )

    def test_pruning_removes_rows_for_systems_we_stopped_running(self):
        self.seed()
        SystemSetting.objects.create(
            institute=self.institute,
            setting_key="voting_student_maintenance_mode",
            setting_value="false",
            system=SystemScope.VOTING,
        )

        call_command("seed_system_settings", "--prune", verbosity=0)

        self.assertNotIn("voting_student_maintenance_mode", self.written_keys())
        self.assertIn("current_school_year", self.written_keys())

    def test_nothing_seeded_falls_through_to_the_coarse_permission(self):
        """The point of the merge.

        Every row an institute gets on day one is governed by a bucket, so
        nobody inherits edit rights over a setting simply because it was never
        classified.
        """
        self.seed()

        for key in SystemSetting.objects.filter(
            institute=self.institute
        ).values_list("setting_key", flat=True):
            with self.subTest(setting=key):
                self.assertNotEqual(
                    setting_buckets.permission_for(key), setting_buckets.BULK
                )

    def test_seeding_twice_changes_nothing(self):
        self.seed()
        SystemSetting.objects.filter(
            institute=self.institute,
            setting_key="collection_officer_maintenance_mode",
        ).update(setting_value="true")

        self.seed()

        row = SystemSetting.objects.get(
            institute=self.institute,
            setting_key="collection_officer_maintenance_mode",
        )
        self.assertEqual(row.setting_value, "true", "a reseed overwrote a decision")

    def test_every_shutter_starts_open(self):
        """A seeded institute must not arrive closed - any of them."""
        self.seed()

        shutters = [
            spec.key
            for spec in setting_buckets.seeded_specs()
            if spec.key.endswith("_maintenance_mode")
        ]
        self.assertTrue(shutters)

        for key in shutters:
            with self.subTest(setting=key):
                row = SystemSetting.objects.get(
                    institute=self.institute, setting_key=key
                )
                self.assertEqual(row.setting_value, "false")

    def test_every_system_gets_both_audiences(self):
        """A system closable to staff but not to students is a half-built one."""
        for system in setting_buckets.SYSTEM_KEYS:
            for audience in setting_buckets.AUDIENCES:
                with self.subTest(system=system, audience=audience):
                    key = setting_buckets.maintenance_key(system, audience)
                    self.assertIsNotNone(setting_buckets.spec_for(key))

    def test_the_two_audiences_are_governed_separately(self):
        """Or the console/desk split this exists for would not hold."""
        for system in setting_buckets.SYSTEM_KEYS:
            with self.subTest(system=system):
                officer = setting_buckets.permission_for(
                    setting_buckets.maintenance_key(system, setting_buckets.OFFICER)
                )
                student = setting_buckets.permission_for(
                    setting_buckets.maintenance_key(system, setting_buckets.STUDENT)
                )
                self.assertEqual(officer, setting_buckets.MAINTENANCE)
                self.assertEqual(student, setting_buckets.STUDENT_MAINTENANCE)
                self.assertNotEqual(officer, student)
