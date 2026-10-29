"""
One command to take an empty database to a working one.

    python manage.py bootstrap

Runs, in order: `makemigrations` (so a model change nobody generated a
migration for does not silently go missing), `migrate` for every app, then
`seed_rbac` so the roles exist - without them every endpoint 403s, because
the whole API is permission-gated.

    python manage.py bootstrap --essential       # + schools .. accounts .. settings
    python manage.py bootstrap --demo            # + a full sample dataset
    python manage.py bootstrap --demo --scale 10 # + ten times the volume
    python manage.py bootstrap --no-input        # never prompt (CI)
    python manage.py bootstrap --check-migrations
        fail if a model change has no migration, instead of creating one

Starting over completely - the "format the database" one:

    python manage.py bootstrap --fresh --essential

`--fresh` drops every table in the configured database and lets `migrate`
build the schema again from nothing. That is the whole database, not just the
seeded rows: accounts, payments and uploads included. It asks for the
database name back before it does anything, refuses outright with DEBUG off
unless `--allow-production` is passed as well, and skips the question only
when `--no-input` says there is nobody to ask.

Safe to re-run without `--fresh`: migrations that are already applied are
skipped, and every seeder is idempotent.
"""

from __future__ import annotations

import os
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Migrate the database and seed the roles (optionally demo data too)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fresh",
            action="store_true",
            help=(
                "Format the database first: drop every table, then migrate "
                "from nothing. Destroys all data."
            ),
        )
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Required alongside --fresh when DEBUG is off.",
        )
        parser.add_argument(
            "--essential",
            action="store_true",
            help=(
                "Also seed the essential steps: schools, institutes, "
                "programs, one account per role, per-institute settings."
            ),
        )
        parser.add_argument(
            "--demo",
            action="store_true",
            help="Also run every demo seeder (seed_all).",
        )
        parser.add_argument(
            "--scale",
            type=int,
            default=1,
            help="Demo volume multiplier; only meaningful with --demo.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="With --demo, delete seeded rows before recreating them.",
        )
        parser.add_argument(
            "--check-migrations",
            action="store_true",
            help=(
                "Fail if a model change has no migration rather than "
                "generating one. Use this in CI."
            ),
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Never prompt.",
        )
        parser.add_argument(
            "--skip-makemigrations",
            action="store_true",
            help="Go straight to migrate.",
        )

    def _step(self, number: int, total: int, title: str) -> None:
        self.stdout.write("")
        self.stdout.write(
            self.style.MIGRATE_HEADING(f"[{number}/{total}] {title}")
        )

    def _format_database(self, *, interactive: bool, allow_production: bool) -> None:
        """Drop every table, so `migrate` can build the schema from nothing.

        Django has `flush`, but `flush` empties tables it can still see - it
        leaves the schema, including tables from a migration that has since
        been rewritten, and it cannot help with the "my migrations and my
        database disagree" mess this exists for. Dropping is the only way to
        actually start over.
        """
        name = str(connection.settings_dict.get("NAME") or "")
        host = connection.settings_dict.get("HOST") or "local file"
        # SQLite's NAME is a path; asking somebody to type one back is a
        # worse question than asking for the file name.
        expected = os.path.basename(name) or name

        if not settings.DEBUG and not allow_production:
            raise CommandError(
                "--fresh drops every table in the database and DEBUG is off. "
                "Pass --allow-production as well if this really is a database "
                "you mean to destroy."
            )

        tables = connection.introspection.table_names()
        if not tables:
            self.stdout.write("    no tables to drop; the database is already empty")
            return

        self.stdout.write(
            self.style.WARNING(
                f"    about to DROP {len(tables)} table(s) from '{name}' on "
                f"{host}. Every row goes with them - accounts, payments, "
                "uploads, everything."
            )
        )

        if interactive:
            try:
                answer = input(f"    type the database name ({expected}) to continue: ")
            except EOFError as exc:
                raise CommandError(
                    "--fresh needs a confirmation and there is nobody to ask. "
                    "Pass --no-input if you mean to skip the question."
                ) from exc
            if answer.strip() != expected:
                raise CommandError("Names did not match; nothing was dropped.")

        quote = connection.ops.quote_name
        vendor = connection.vendor
        cascade = " CASCADE" if vendor == "postgresql" else ""

        with connection.cursor() as cursor:
            # Tables reference each other, so there is no order that drops
            # them cleanly. Turning the checks off for the duration is the
            # usual way; both statements are session-scoped.
            if vendor == "mysql":
                cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
            elif vendor == "sqlite":
                cursor.execute("PRAGMA foreign_keys = OFF")

            try:
                for table in tables:
                    cursor.execute(f"DROP TABLE IF EXISTS {quote(table)}{cascade}")
            finally:
                if vendor == "mysql":
                    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
                elif vendor == "sqlite":
                    cursor.execute("PRAGMA foreign_keys = ON")

        self.stdout.write(self.style.SUCCESS(f"    dropped {len(tables)} table(s)"))

    def _pending_migrations(self) -> int:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        return len(executor.migration_plan(targets))

    def handle(self, *args, **options):
        started = time.monotonic()
        seeds_data = options["demo"] or options["essential"]
        total = 3 + int(options["fresh"]) + int(seeds_data)
        step = 0

        # 0. Format ---------------------------------------------------------
        if options["fresh"]:
            step += 1
            self._step(step, total, "Formatting the database")
            self._format_database(
                interactive=not options["no_input"],
                allow_production=options["allow_production"],
            )

        # 1. Model changes without a migration -----------------------------
        step += 1
        self._step(step, total, "Checking for model changes")
        if options["skip_makemigrations"]:
            self.stdout.write("    skipped (--skip-makemigrations)")
        elif options["check_migrations"]:
            try:
                call_command("makemigrations", check=True, dry_run=True, verbosity=1)
            except SystemExit as exc:
                # `--check` exits non-zero rather than raising, which would
                # otherwise take the whole process down mid-bootstrap.
                if exc.code:
                    raise CommandError(
                        "Model changes have no migration. Run "
                        "`python manage.py makemigrations` and commit the result."
                    ) from exc
            self.stdout.write("    no missing migrations")
        else:
            call_command(
                "makemigrations", interactive=not options["no_input"], verbosity=1
            )

        # 2. Apply them ----------------------------------------------------
        step += 1
        pending = self._pending_migrations()
        self._step(step, total, f"Applying migrations ({pending} pending)")
        call_command("migrate", interactive=not options["no_input"], verbosity=1)

        # 3. Roles ---------------------------------------------------------
        step += 1
        self._step(step, total, "Seeding roles and permissions")
        call_command("seed_rbac", sync_staff_flag=True)

        # 4. The rest of the data ------------------------------------------
        # `--demo` wins over `--essential`: it is the wider set, and asking
        # for both should not mean seeding less than either alone.
        if seeds_data:
            step += 1
            essential_only = options["essential"] and not options["demo"]
            self._step(
                step,
                total,
                "Seeding the essentials" if essential_only else "Seeding demo data",
            )
            call_command(
                "seed_all",
                scale=options["scale"],
                reset=options["reset"],
                essential=essential_only,
            )

        elapsed = time.monotonic() - started
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Bootstrap complete in {elapsed:.1f}s.")
        )
        if seeds_data:
            self.stdout.write(
                "Sign in as `superadmin` / `password123`, or as one of the "
                "accounts named after the rows they belong to - `dnsc_admin`, "
                "`dnsc_ic_admin` (see `python manage.py seed_users --help`)."
            )
        else:
            self.stdout.write(
                "Database is migrated and the roles exist. Create your first "
                "account with `python manage.py createsuperuser`, or run "
                "`python manage.py bootstrap --essential` for the schools, "
                "institutes, programs and accounts to sign in with."
            )
