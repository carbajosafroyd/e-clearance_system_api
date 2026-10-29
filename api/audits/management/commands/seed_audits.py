"""
Seed a short audit trail so the log screen and its report are not empty.

Entries are written through `AuditLogService.log_action`, the same entry
point the apps use, and are dated across the last two weeks so the report's
per-day chart has a shape.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import resolve_actor
from api.fees.models import Fee
from api.payments.models import Payment
from api.rbac.roles import Roles
from api.seeding.support import SeedCommand
from api.users.models import User

#: (entity_type, action, system) triples worth having in the trail.
SHAPES = [
    ("Fee", AuditLog.Action.UPDATE, AuditLog.System.COLLECTION),
    ("Fee", AuditLog.Action.DELETE, AuditLog.System.COLLECTION),
    ("Payment", AuditLog.Action.CANCEL, AuditLog.System.COLLECTION),
    ("PaymentSubmission", AuditLog.Action.APPROVE, AuditLog.System.COLLECTION),
    ("PaymentSubmission", AuditLog.Action.REJECT, AuditLog.System.COLLECTION),
    ("LockerSubscription", AuditLog.Action.CREATE, AuditLog.System.LOCKER),
    ("AttendanceRecord", AuditLog.Action.UPDATE, AuditLog.System.ATTENDANCE),
    ("StudentClearanceItem", AuditLog.Action.APPROVE, AuditLog.System.CLEARANCE),
    ("UserRoles", AuditLog.Action.UPDATE, AuditLog.System.PLATFORM),
]

ENTRIES_PER_SHAPE = 4


class Command(SeedCommand):
    seed_name = "audit logs"
    help = "Create a short demo audit trail."
    depends_on = ("seed_users",)

    def reset(self, **options):
        deleted, _ = AuditLog.objects.all().delete()
        self.warn(f"deleted {deleted} audit log row(s)")

    def seed(self, **options):
        actors = list(
            User.objects.filter(
                groups__name__in=[
                    Roles.CMS_ADMIN,
                    Roles.CMS_STAFF,
                    Roles.SCHOOL_ADMIN,
                ]
            ).order_by("id")
        )
        if not actors:
            self.warn("no staff accounts; run `python manage.py seed_users` first")
            return

        # The trail has no natural key to get_or_create on, so idempotency
        # is a count check: once it holds what this seeder would produce,
        # there is nothing to add.
        wanted = len(SHAPES) * ENTRIES_PER_SHAPE * self.scale
        if AuditLog.objects.count() >= wanted:
            self.info(f"audit trail already holds {wanted}+ entries; nothing to do")
            return

        fee_ids = list(Fee.objects.values_list("id", flat=True)[:100]) or [1]
        payment_ids = list(Payment.objects.values_list("id", flat=True)[:100]) or [1]

        created = 0
        now = timezone.now()

        for entity_type, action, system in SHAPES:
            for index in range(ENTRIES_PER_SHAPE * self.scale):
                actor = self.pick(actors)
                actor_type, actor_id = resolve_actor(actor)

                entity_id = (
                    self.pick(payment_ids)
                    if entity_type == "Payment"
                    else self.pick(fee_ids)
                )

                entry = AuditLogService.log_action(
                    system=system,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    action=action,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    old_values={"status": "pending"},
                    new_values={"status": str(action)},
                )

                # created_at is auto_now_add, so the spread across the last
                # fortnight has to be applied afterwards with an update.
                AuditLog.objects.filter(pk=entry.pk).update(
                    created_at=now - timedelta(days=index % 14, hours=index)
                )
                created += 1

        self.created("audit log entry", created)
