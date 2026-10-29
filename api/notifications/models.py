from django.db import models
from api.institutes.models import Institute
from api.programs.models import Program
from api.users.models import User
from core.BaseModel import TimeStampedModel

class NotificationBroadcast(TimeStampedModel):
    sender = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="broadcasts"
    )
    title = models.CharField(max_length=255)
    message = models.TextField()

    target_institute = models.ForeignKey(
        Institute, on_delete=models.SET_NULL, blank=True, null=True
    )
    target_program = models.ForeignKey(
        Program, on_delete=models.SET_NULL, blank=True, null=True
    )
    target_year_level = models.IntegerField(blank=True, null=True)

    target_all_users = models.BooleanField(blank=True, null=True)

    total_recipients = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notification_broadcast"
        permissions = [
            ("send_notificationbroadcast", "Can send a notification broadcast"),
        ]
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["target_program", "target_year_level"]),
        ]

    def __str__(self):
        return f"Broadcast: {self.title[:30]} by {self.sender} ({self.total_recipients} users)"


class Notification(TimeStampedModel):
    class NotificationType(models.TextChoices):
        PENDING_FEES = "fees.pending", "Pending Fees"
        PAYMENT_APPROVED = "payment.approved", "Payment Approved"
        PAYMENT_REJECTED = "payment.rejected", "Payment Rejected"
        SYSTEM_ANNOUNCEMENT = "system.announcement", "System Announcement"
        COMPLAINT_ACKNOWLEDGED = "complaint.acknowledged", "Complaint Acknowledged"
        COMPLAINT_RESOLVED = "complaint.resolved", "Complaint Resolved"
        COMPLAINT_REJECTED = "complaint.rejected", "Complaint Closed"
        CLEARANCE_ISSUE = "clearance.issue", "Clearance Deficiency Issue"
        CLEARANCE_PROOF_SUBMITTED = "clearance.proof_submitted", "Clearance Proof Submitted"
        CLEARANCE_CLEARED = "clearance.cleared", "Clearance Item Cleared"
        CLEARANCE_COMPLETED = "clearance.completed", "Clearance Completed"

    class NotificationSource(models.TextChoices):
        COLLECTION = "collection", "Collection Management System"
        FINANCE = "finance", "Finance System"
        ENROLLMENT = "enrollment", "Enrollment System"
        SUBMISSIONS = "submissions", "Submissions System"
        AUTH = "auth", "Authentication System"
        COMPLAINTS = "complaints", "Complaints"
        CLEARANCE = "clearance", "Clearance System"
        SYSTEM = "system", "System"

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    sender = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_notifications",
    )
    source = models.CharField(max_length=30, choices=NotificationSource.choices)

    title = models.CharField(max_length=255)
    message = models.TextField()
    type = models.CharField(max_length=50, choices=NotificationType.choices)
    data = models.JSONField(null=True, blank=True)

    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    broadcast = models.ForeignKey(
        NotificationBroadcast,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )

    class Meta:
        db_table = "notifications"
        indexes = [
            models.Index(fields=["user", "is_read"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["source"]),
            models.Index(fields=["type"]),
            models.Index(fields=["broadcast"]),
            models.Index(fields=["broadcast", "is_read"]),
        ]
