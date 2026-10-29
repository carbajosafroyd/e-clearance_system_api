"""
Seed notifications: a few per-student ones and one institute-wide broadcast.

Goes through `notifications.service` so the rows look exactly like the ones
the app writes - same types, same sources, same broadcast linkage.
"""

from __future__ import annotations

from decimal import Decimal

from api.fees.models import Fee
from api.institutes.models import Institute
from api.notifications.models import Notification, NotificationBroadcast
from api.notifications.service import notify_user
from api.rbac.roles import Roles
from api.seeding.support import SeedCommand
from api.users.models import User


class Command(SeedCommand):
    seed_name = "notifications"
    help = "Create demo notifications and one broadcast per institute."
    depends_on = ("seed_users", "seed_fees")

    def reset(self, **options):
        Notification.objects.all().delete()
        deleted, _ = NotificationBroadcast.objects.all().delete()
        self.warn(f"deleted {deleted} broadcast row(s) and every notification")

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")

        created_broadcasts = 0
        created_notifications = 0

        for institute in institutes:
            sender = (
                User.objects.filter(
                    institute=institute,
                    groups__name__in=[Roles.CMS_ADMIN, Roles.SCHOOL_ADMIN],
                )
                .order_by("id")
                .first()
            )
            if sender is None:
                continue

            recipients = list(
                User.objects.filter(
                    institute=institute, groups__name=Roles.STUDENT
                ).order_by("id")[: 10 * self.scale]
            )
            if not recipients:
                continue

            broadcast, made = NotificationBroadcast.objects.get_or_create(
                title="Collection period is open",
                target_institute=institute,
                defaults={
                    "sender": sender,
                    "message": (
                        "Settle your outstanding balances at the collection "
                        "office or through GCash before the due date."
                    ),
                    "total_recipients": len(recipients),
                },
            )
            created_broadcasts += int(made)

            if made:
                Notification.objects.bulk_create(
                    [
                        Notification(
                            user=recipient,
                            sender=sender,
                            source=Notification.NotificationSource.COLLECTION,
                            title=broadcast.title,
                            message=broadcast.message,
                            type=Notification.NotificationType.SYSTEM_ANNOUNCEMENT,
                            broadcast=broadcast,
                        )
                        for recipient in recipients
                    ]
                )
                created_notifications += len(recipients)

            created_notifications += self._pending_fee_notices(recipients, sender)

        self.created("broadcast", created_broadcasts)
        self.created("notification", created_notifications)

    def _pending_fee_notices(self, recipients: list[User], sender: User) -> int:
        created = 0
        for user in self.sample(recipients, max(1, len(recipients) // 3)):
            student = getattr(user, "student", None)
            if student is None:
                continue

            # These have no natural key either, so "already told them once"
            # is the idempotency check.
            if Notification.objects.filter(
                user=user, type=Notification.NotificationType.PENDING_FEES
            ).exists():
                continue

            outstanding = Fee.objects.filter(
                student=student,
                status__in=[Fee.FeeStatus.PENDING, Fee.FeeStatus.OVERDUE],
            )
            total = sum((fee.balance for fee in outstanding), Decimal("0.00"))
            if total <= 0:
                continue

            notify_user(
                user=user,
                sender=sender,
                title="You have unsettled fees",
                message=(
                    f"You have {outstanding.count()} unsettled fee(s) totalling "
                    f"PHP {total:,.2f}."
                ),
                type=Notification.NotificationType.PENDING_FEES,
                source=Notification.NotificationSource.COLLECTION,
                data={"total_balance": str(total)},
            )
            created += 1
        return created
