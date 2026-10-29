"""
Seed notifications: sample clearance notifications and one broadcast per institute.

Goes through `notifications.service` so the rows look exactly like the ones
the app writes - same types, same sources, same broadcast linkage.
"""

from __future__ import annotations

from api.institutes.models import Institute
from api.notifications.models import Notification, NotificationBroadcast
from api.notifications.service import notify_user
from api.rbac.roles import Roles
from api.seeding.support import SeedCommand
from api.users.models import User


class Command(SeedCommand):
    seed_name = "notifications"
    help = "Create demo clearance notifications and one broadcast per institute."
    depends_on = ("seed_users", "seed_clearance")

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
                    groups__name__in=[Roles.CLEARANCE_ADMIN, Roles.INSTITUTE_ADMIN, Roles.SCHOOL_ADMIN],
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
                title="E-Clearance Period is Now Open",
                target_institute=institute,
                defaults={
                    "sender": sender,
                    "message": (
                        "The clearance signing window is now active. Please check "
                        "your clearance requirements, settle obligations, and submit proofs."
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
                            source=Notification.NotificationSource.CLEARANCE,
                            title=broadcast.title,
                            message=broadcast.message,
                            type=Notification.NotificationType.SYSTEM_ANNOUNCEMENT,
                            broadcast=broadcast,
                        )
                        for recipient in recipients
                    ]
                )
                created_notifications += len(recipients)

            created_notifications += self._sample_clearance_notices(recipients, sender)

        self.created("broadcast", created_broadcasts)
        self.created("notification", created_notifications)

    def _sample_clearance_notices(self, recipients: list[User], sender: User) -> int:
        created = 0
        for user in self.sample(recipients, max(1, len(recipients) // 3)):
            student = getattr(user, "student", None)
            if student is None:
                continue

            if Notification.objects.filter(
                user=user, type=Notification.NotificationType.CLEARANCE_ISSUE
            ).exists():
                continue

            notify_user(
                user=user,
                sender=sender,
                title="Clearance Requirement Pending",
                message="You have pending clearance items requiring settlement or proof submission.",
                type=Notification.NotificationType.CLEARANCE_ISSUE,
                source=Notification.NotificationSource.CLEARANCE,
                data={"student_id": student.id},
            )
            created += 1
        return created
