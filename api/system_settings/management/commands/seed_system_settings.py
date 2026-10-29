"""
Seed the per-institute key/value settings the frontend reads on boot.

`current_school_year` and `current_semester` are the important two: the
frontend seeds every list filter and report period from them, so an institute
without them shows unfiltered lists everywhere.

What to seed is not decided here. `core.setting_buckets.CATALOG` declares each
setting once - its key, the permission that governs writing it, its starting
value and its description - and this command walks it. Keeping the list here
as well is how the seeder and the permission map used to disagree about which
settings exist, which is silent in both directions: a row nobody may edit, or
a rule about a row nobody creates.
"""

from __future__ import annotations

from api.institutes.models import Institute
from api.seeding.support import SeedCommand
from api.system_settings.models import SystemSetting
from core import features, setting_buckets

#: One row per optional module, so the toggles exist to be flipped without a
#: deploy. Seeded per institute and only where the institute may run the
#: module at all - writing a row for a feature it is not entitled to would
#: put a dead switch on its settings page.
FEATURE_DESCRIPTIONS = {
    features.CONTRIBUTIONS: (
        "Student reports and recommendations. Off means the whole module is "
        "gone, including the points half."
    ),
    features.CONTRIBUTION_POINTS: (
        "The reward half: points, wallets, the ranking and points-funded fee "
        "discounts. Needs Contributions on. Off means reports are still "
        "filed and reviewed, they just do not pay."
    ),
    features.COMPLAINTS: (
        "Student complaints and the staff workflow that resolves them. "
        "Independent of the contribution scheme."
    ),
    features.COUPONS: (
        "Coupons handed out at events, redeemed against a fee. Independent "
        "of contribution points; a fee may carry one of each."
    ),
}


class Command(SeedCommand):
    seed_name = "system settings"
    help = "Create the per-institute settings the frontend reads on boot."
    depends_on = ("seed_institutes",)

    def add_seed_arguments(self, parser):
        parser.add_argument(
            "--prune",
            action="store_true",
            help=(
                "Also delete rows for systems this deployment does not run "
                "(see LIVE_SYSTEMS). Values are discarded."
            ),
        )

    def reset(self, **options):
        keys = list(setting_buckets.catalog_keys()) + list(
            features.known_feature_keys()
        )
        deleted, _ = SystemSetting.objects.filter(setting_key__in=keys).delete()
        self.warn(f"deleted {deleted} system setting row(s)")

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")

        if options.get("prune"):
            self.prune()

        specs = setting_buckets.seeded_specs()
        created = 0
        for institute in institutes:
            # `setting_buckets.CATALOG` is the one list. A setting declared
            # there is seeded here and governed by a permission there, so the
            # two cannot describe different sets of settings.
            #
            # `seeded_specs()` narrows it to the systems this deployment
            # actually serves. The rest stay declared - so the permission map
            # has an answer the day a row appears - but are not written,
            # because a shutter for a system nobody has built yet is a switch
            # that does nothing when somebody flips it.
            for spec in specs:
                _, made = SystemSetting.objects.get_or_create(
                    institute=institute,
                    setting_key=spec.key,
                    defaults={
                        "setting_value": spec.initial_value(),
                        "description": spec.description,
                        "system": spec.system,
                    },
                )
                created += int(made)

            created += self.seed_feature_flags(institute)

        self.created("system setting", created)

    def prune(self):
        """Drop rows for systems this deployment does not serve.

        Separate from `--reset`, which deletes everything and starts over.
        This only removes switches that were never connected to anything -
        the ones an earlier seed wrote for systems that had no frontend.
        """
        running = set(setting_buckets.live_systems())
        dead = [
            system
            for system in setting_buckets.SYSTEM_KEYS
            if system not in running
        ]
        if not dead:
            return

        deleted, _ = SystemSetting.objects.filter(system__in=dead).delete()
        if deleted:
            self.warn(
                f"pruned {deleted} row(s) for systems this deployment does "
                f"not run: {', '.join(dead)}"
            )

    def seed_feature_flags(self, institute) -> int:
        """A row per module this institute may run.

        Seeded at the environment's own default, so an existing deployment
        behaves exactly as it did before the rows existed - the first run of
        this writes down what was already true rather than changing anything.
        """
        created = 0
        for feature in features.ALL_FEATURES:
            if not features.is_available(feature, institute):
                continue
            _, made = SystemSetting.objects.get_or_create(
                institute=institute,
                setting_key=features.setting_key(feature),
                defaults={
                    "setting_value": str(
                        features.ships_with_build(feature)
                    ).lower(),
                    "description": FEATURE_DESCRIPTIONS.get(feature, ""),
                    "system": setting_buckets.FEATURE_SYSTEM,
                },
            )
            created += int(made)
        return created
