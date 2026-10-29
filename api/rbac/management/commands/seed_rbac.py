"""
Create the project's roles and hand each one its permissions.

    python manage.py seed_rbac                    # create/update roles
    python manage.py seed_rbac --sync-staff-flag  # also fix User.is_staff
    python manage.py seed_rbac --dry-run          # show the plan, change nothing
    python manage.py seed_rbac --prune            # delete groups not in roles.py

Idempotent: safe on a fresh database and safe on a live one. Run it after
every migration that adds a permission.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from api.rbac.roles import ROLE_DEFINITIONS, resolve_permissions
from api.rbac.services import expand, sync_roles, sync_staff_flags


class Command(BaseCommand):
    help = "Create the roles declared in api/rbac/roles.py and set their permissions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete groups that roles.py does not declare.",
        )
        parser.add_argument(
            "--sync-staff-flag",
            action="store_true",
            help="Set is_staff on members of roles marked staff_flag.",
        )
        parser.add_argument(
            "--demote-staff",
            action="store_true",
            help=(
                "With --sync-staff-flag, also clear is_staff on non-superusers "
                "who are in none of those roles."
            ),
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            self._dry_run()
            return

        report = sync_roles(prune_unknown_groups=options["prune"])

        for name in report.created_groups:
            self.stdout.write(self.style.SUCCESS(f"  + created  {name}"))
        for name in report.updated_groups:
            self.stdout.write(f"  ~ updated  {name}")
        for name in report.unchanged_groups:
            self.stdout.write(f"    ok       {name}")
        for name in report.removed_groups:
            self.stdout.write(self.style.WARNING(f"  - removed  {name}"))

        if report.missing_permissions:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "These permissions are named in roles.py but do not exist "
                    "in the database. Either the codename is a typo, or the "
                    "migration that adds it has not been applied:"
                )
            )
            for name in sorted(report.missing_permissions):
                self.stdout.write(self.style.WARNING(f"    ! {name}"))

        if options["sync_staff_flag"]:
            promoted, demoted = sync_staff_flags(demote=options["demote_staff"])
            self.stdout.write("")
            self.stdout.write(
                f"is_staff: {promoted} promoted, {demoted} demoted."
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"RBAC seeded: {len(ROLE_DEFINITIONS)} role(s) in sync."
            )
        )

    def _dry_run(self):
        with transaction.atomic():
            for role_name in ROLE_DEFINITIONS:
                specs = resolve_permissions(role_name)
                permissions, missing = expand(specs)
                self.stdout.write(
                    f"{role_name}: {len(permissions)} permission(s)"
                    + (f", {len(missing)} missing" if missing else "")
                )
                for name in sorted(missing):
                    self.stdout.write(self.style.WARNING(f"    ! {name}"))
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Dry run - nothing was written."))
