from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Deletes expired and/or used transfer tokens. Intended to be run "
        "periodically via cron (or Celery beat) to keep the table small, "
        "since rows are only useful for a few seconds after issuance."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--grace-seconds",
            type=int,
            default=getattr(settings, "TRANSFER_TOKEN_CLEANUP_GRACE_SECONDS", 300),
            help=(
                "Only delete tokens that expired at least this many seconds "
                "ago (default: 300 / 5 minutes). Used tokens are always "
                "eligible for deletion regardless of this grace period. "
                "This avoids any edge-case overlap with an in-flight redeem "
                "request right at the expiry boundary."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many rows would be deleted without deleting them.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress output when there is nothing to delete (cron-friendly).",
        )

    def handle(self, *args, **options):
        from transfer_token.models import TransferToken

        grace_seconds = options["grace_seconds"]
        dry_run = options["dry_run"]
        quiet = options["quiet"]

        cutoff = timezone.now() - timezone.timedelta(seconds=grace_seconds)

        qs = TransferToken.objects.filter(
            Q(expires_at__lt=cutoff) | Q(used_at__isnull=False)
        )
        count = qs.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[dry-run] Would delete {count} expired/used transfer token(s)."
                )
            )
            return

        if count:
            qs.delete()
            self.stdout.write(
                self.style.SUCCESS(f"Deleted {count} expired/used transfer token(s).")
            )
        elif not quiet:
            self.stdout.write("No expired/used transfer tokens to delete.")