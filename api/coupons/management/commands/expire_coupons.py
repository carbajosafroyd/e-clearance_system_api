"""
Flip past-window coupons to EXPIRED.

A sweep rather than a status derived on read, mirroring
`fees/update-overdue-fees/`: deriving it would make every list query and
every count reason about the clock, and a coupon's row should be able to say
what it is. Safe to run as often as you like - it only ever moves ACTIVE
coupons whose batch window has closed.
"""

from django.core.management.base import BaseCommand

from api.coupons import services


class Command(BaseCommand):
    help = "Mark coupons expired once their batch window has closed."

    def handle(self, *args, **options):
        moved = services.expire_due_coupons()
        self.stdout.write(
            self.style.SUCCESS(f"{moved} coupon(s) marked expired.")
        )
