"""
How many accounts still sit on each pepper version.

Run it before removing an old PASSWORD_PEPPER_<N> from the environment: a
row still on that version can only be verified with it, so once it is gone
that account's only way back is a password reset. Rows on version 0 are
hashes from before the pepper existed; both kinds move to the current
version on the user's next successful sign-in, which is the only time the
plaintext is available to re-hash.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Count

from core import pepper


class Command(BaseCommand):
    help = "Count user rows per password-pepper version; run before retiring one."

    def handle(self, *args, **options):
        User = get_user_model()
        current = pepper.current_version()
        known = set(settings.PASSWORD_PEPPERS)

        self.stdout.write(
            f"Current pepper version: {current}"
            + ("" if current else "  (no pepper configured - hashes are bare)")
        )
        self.stdout.write(f"Configured versions: {sorted(known) or '-'}")
        self.stdout.write("")

        rows = (
            User.objects.values("pepper_version")
            .annotate(n=Count("id"))
            .order_by("pepper_version")
        )
        if not rows:
            self.stdout.write("No users.")
            return

        for row in rows:
            version, n = row["pepper_version"], row["n"]
            if version == current:
                note = "current"
            elif version == pepper.LEGACY_VERSION:
                note = "no pepper - upgrades on next sign-in"
            elif version in known:
                note = "old pepper still configured - upgrades on next sign-in"
            else:
                note = "PEPPER MISSING - these accounts need a password reset"
            style = self.style.WARNING if version not in known and version != current else str
            self.stdout.write(style(f"  version {version}: {n:>6} user(s)   {note}"))
