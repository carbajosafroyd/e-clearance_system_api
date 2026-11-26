"""Read-only audit (and optional cleanup) for admin vs signatory role separation."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from api.rbac.roles import is_admin_group_name, is_signatory_group_name


class Command(BaseCommand):
    help = (
        "Check if any superuser or admin account currently holds a clearance "
        "signatory group. Read-only unless --fix is explicitly passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help="Remove signatory groups from conflicting admin/superuser accounts.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        fix = options["fix"]
        conflicts = []

        for user in User.objects.prefetch_related("groups").order_by("id"):
            group_names = list(user.groups.values_list("name", flat=True))
            admin_groups = [name for name in group_names if is_admin_group_name(name)]
            signatory_groups = [name for name in group_names if is_signatory_group_name(name)]

            if signatory_groups and (user.is_superuser or admin_groups):
                conflicts.append((user, admin_groups, signatory_groups))

        if not conflicts:
            self.stdout.write(
                self.style.SUCCESS(
                    "OK: No superuser or admin accounts hold any signatory group."
                )
            )
            return

        self.stdout.write(
            self.style.WARNING(
                f"Found {len(conflicts)} account(s) violating admin/signatory separation:"
            )
        )
        for user, admin_groups, signatory_groups in conflicts:
            self.stdout.write(
                f"  - id={user.pk} username={user.username!r} "
                f"is_superuser={user.is_superuser} "
                f"admin_groups={admin_groups} signatory_groups={signatory_groups}"
            )

            if fix:
                to_remove = user.groups.filter(name__in=signatory_groups)
                user.groups.remove(*to_remove)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    -> Removed signatory group(s) {signatory_groups} from {user.username!r}."
                    )
                )
