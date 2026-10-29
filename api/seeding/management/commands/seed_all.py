"""
Run every seeder, in dependency order.

    python manage.py seed_all                 # roles + a demo dataset
    python manage.py seed_all --essential     # the minimum a new database needs
    python manage.py seed_all --core-only     # roles only (safe in production)
    python manage.py seed_all --scale 10      # ten times the demo volume
    python manage.py seed_all --reset         # wipe seeded rows first
    python manage.py seed_all --only seed_fees seed_payments

`--essential` is the one to reach for on a brand-new database: roles,
schools, institutes, programs, the accounts to sign in with, and the
per-institute settings the frontend reads on boot - and none of the sample
fees, payments or complaints. Which steps those are is `essential=True` in
the registry.

A step marked `supports_essential` is asked to seed less as well, rather than
only being included: `seed_users --essential` creates the superuser, a School
Admin per school and an Institute Admin per institute, and leaves the demo
collection, clearance and locker staff out.

The order lives in `api/seeding/registry.py`. Each step is idempotent, so
re-running is safe and fills in whatever is missing. Steps belonging to a
switched-off feature are skipped and reported, so a deployment that does not
run a module never grows rows for it.
"""

from __future__ import annotations

import time

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from api.seeding.registry import SEED_STEPS


class Command(BaseCommand):
    help = "Run every seeder in dependency order."

    def add_arguments(self, parser):
        parser.add_argument(
            "--core-only",
            action="store_true",
            help="Only the steps marked core (roles). No sample data.",
        )
        parser.add_argument(
            "--essential",
            action="store_true",
            help=(
                "Only the essential steps: roles, schools, institutes, "
                "programs, accounts and per-institute settings."
            ),
        )
        parser.add_argument(
            "--scale",
            type=int,
            default=1,
            help="Volume multiplier passed to each seeder that supports it.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Ask each seeder to delete its rows before recreating them.",
        )
        parser.add_argument(
            "--only",
            nargs="+",
            metavar="COMMAND",
            help="Run just these steps, still in registry order.",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="Report a failing step and carry on instead of stopping.",
        )

    def handle(self, *args, **options):
        wanted = options.get("only")
        if wanted:
            known = {step.command for step in SEED_STEPS}
            unknown = [name for name in wanted if name not in known]
            if unknown:
                raise CommandError(
                    f"Unknown step(s): {', '.join(unknown)}. "
                    f"Known steps: {', '.join(sorted(known))}."
                )

        selected = [
            step
            for step in SEED_STEPS
            if (not wanted or step.command in wanted)
            and (not options["core_only"] or step.kind == "core")
            and (not options["essential"] or step.essential)
        ]

        steps = [step for step in selected if step.enabled]
        skipped = [step for step in selected if not step.enabled]

        for step in skipped:
            self.stdout.write(
                self.style.WARNING(
                    f"  skipping {step.command}: the '{step.feature}' feature "
                    "is switched off for this deployment."
                )
            )

        started = time.monotonic()
        failures: list[tuple[str, str]] = []

        for index, step in enumerate(steps, start=1):
            self.stdout.write("")
            self.stdout.write(
                self.style.MIGRATE_HEADING(
                    f"[{index}/{len(steps)}] {step.command} - {step.description}"
                )
            )

            kwargs: dict = {}
            if step.supports_scale:
                kwargs["scale"] = options["scale"]
                kwargs["reset"] = options["reset"]
            if options["essential"] and step.supports_essential:
                # The step is essential *and* has a smaller essential mode of
                # its own - `seed_users` seeds the admins and skips the demo
                # collection, clearance and locker staff.
                kwargs["essential"] = True

            try:
                call_command(step.command, **kwargs)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                if not options["continue_on_error"]:
                    raise
                failures.append((step.command, str(exc)))
                self.stdout.write(self.style.ERROR(f"    failed: {exc}"))

        elapsed = time.monotonic() - started
        self.stdout.write("")

        if failures:
            self.stdout.write(self.style.ERROR(f"{len(failures)} step(s) failed:"))
            for command, message in failures:
                self.stdout.write(self.style.ERROR(f"  - {command}: {message}"))
            raise CommandError("seed_all finished with errors.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(steps)} step(s) in {elapsed:.1f}s."
            )
        )
        if not options["core_only"]:
            self.stdout.write(
                "Demo accounts all use the password 'password123' - see "
                "`python manage.py seed_users --help`."
            )
