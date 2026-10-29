from django.db import models
from api.institutes.models import Institute
from core.BaseModel import ActorStampedModel
from config import settings
from core.choices import AcademicYear, get_default_academic_year, Semester
from rest_framework.serializers import ValidationError


class Locker(ActorStampedModel):
    locker_number = models.CharField(max_length=20, unique=True)
    building = models.ForeignKey(
        "schools.SchoolBuilding", on_delete=models.CASCADE, related_name="lockers"
    )
    institute = models.ForeignKey(
        Institute, on_delete=models.CASCADE, related_name="lockers", null=True
    )
    floor = models.PositiveIntegerField(null=False, blank=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "lockers"
        unique_together = ("building", "floor", "locker_number")
        permissions = [
            ("bulk_add_locker", "Can create lockers in bulk"),
            ("bulk_delete_locker", "Can delete lockers in bulk"),
            ("view_locker_summary", "Can view locker occupancy metrics"),
        ]
        indexes = [
            models.Index(fields=["building", "floor", "is_active"]),
        ]

    def __str__(self):
        return self.locker_number

class LockerSubscriptionRequest(ActorStampedModel):
    class SubscriptionStatus(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        CANCELLED = "cancelled"

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="locker_subscriptions_request",
    )
    locker = models.ForeignKey(Locker, on_delete=models.CASCADE)
    academic_year = models.CharField(
        max_length=9, choices=AcademicYear.choices, default=get_default_academic_year
    )
    semester = models.CharField(
        max_length=20, choices=Semester.choices, default=Semester.FIRST
    )
    status = models.CharField(
        max_length=15,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.PENDING,
    )

    class Meta:
        db_table = "locker_subscription_request"
        permissions = [
            ("approve_lockersubscriptionrequest", "Can approve a locker request"),
        ]
        indexes = [
            models.Index(fields=["academic_year", "semester"]),
            models.Index(fields=["status", "academic_year"]),
            models.Index(fields=["student", "academic_year", "semester"]),
        ]
    def __str__(self):
        return f"id: {self.id} | student: {self.student} -> Locker Number: {self.locker.locker_number} in {self.academic_year} | {self.semester}"


class LockerSubscription(ActorStampedModel):
    class SubscriptionStatus(models.TextChoices):
        ACTIVE = "active"
        CANCELLED = "cancelled"
        EXPIRED = "expired"

    class SubscriptionPaymentStatus(models.TextChoices):
        UNPAID = "unpaid"
        PAID = "paid"
        REFUND_PENDING = "refund_pending"
        REFUNDED = "refunded"

    student = models.ForeignKey(
        "students.Student",
        on_delete=models.CASCADE,
        related_name="locker_subscriptions",
    )
    locker = models.ForeignKey(Locker, on_delete=models.CASCADE)
    fee = models.OneToOneField(
        "fees.Fee",
        on_delete=models.CASCADE,
        null=True,
        related_name="locker_subscription",
    )
    subscribed_at = models.DateField(auto_now_add=True)
    academic_year = models.CharField(
        max_length=9, choices=AcademicYear.choices, default=get_default_academic_year
    )
    semester = models.CharField(
        max_length=20, choices=Semester.choices, default=Semester.FIRST
    )
    status = models.CharField(
        max_length=15,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
    )
    payment_status = models.CharField(
        max_length=15,
        choices=SubscriptionPaymentStatus.choices,
        default=SubscriptionPaymentStatus.UNPAID,
    )
    payment_due_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    subscription_request = models.OneToOneField(
        LockerSubscriptionRequest,
        on_delete=models.CASCADE,
        related_name="locker_subscription",
        null=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    cancellation_reason = models.CharField(max_length=100, null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "locker_subscriptions"
        permissions = [
            ("cancel_lockersubscription", "Can cancel a locker subscription"),
        ]
        indexes = [
            models.Index(fields=["academic_year", "semester"]),
            models.Index(fields=["status", "academic_year"]),
            models.Index(fields=["payment_status", "payment_due_at"]),
            models.Index(fields=["student", "academic_year", "semester"]),
            models.Index(fields=["-subscribed_at"]),
        ]
    def __str__(self):
        return f"{self.student} -> {self.locker}"

class LockerReport(ActorStampedModel):
    locker = models.ForeignKey(Locker, on_delete=models.CASCADE)
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="locker_reports")
    message = models.TextField(default='')

    class Meta:
        db_table = "locker_reports"
        indexes = [
            models.Index(fields=["locker", "student"])
        ]
    def __str__  (self):
        return f"Locker {self.locker} | {self.student} | message: {self.message}"