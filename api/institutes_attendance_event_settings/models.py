from django.db import models
from jsonschema import ValidationError
from core.BaseModel import ActorStampedModel
from api.institutes_attendance_events.models import InstituteAttendanceEvent


class InstituteAttendanceEventSetting(ActorStampedModel):
    class EventStatus(models.TextChoices):
        UPCOMING = "upcoming", "Upcoming"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    institute_attendance_event = models.ForeignKey(
        InstituteAttendanceEvent, on_delete=models.CASCADE, related_name="settings"
    )
    date = models.DateField(db_index=True)

    has_morning_in = models.BooleanField(default=False)
    has_morning_out = models.BooleanField(default=False)
    has_afternoon_in = models.BooleanField(default=False)
    has_afternoon_out = models.BooleanField(default=False)

    checkin_start = models.TimeField(null=True)
    checkin_end = models.TimeField(null=True)
    checkout_start = models.TimeField(null=True)
    checkout_end = models.TimeField(null=True)

    afternoon_checkin_start = models.TimeField(null=True)
    afternoon_checkin_end = models.TimeField(null=True)
    afternoon_checkout_start = models.TimeField(null=True)
    afternoon_checkout_end = models.TimeField(null=True)

    event_status = models.CharField(
        max_length=20,
        choices=EventStatus.choices,
        default=EventStatus.UPCOMING,
        db_index=True,
    )

    def clean(self):
        if self.has_morning_in and (
            self.checkin_start is None or self.checkin_end is None
        ):
            raise ValidationError(
                "Morning checkin times must be set if has_morning_in=True"
            )
        if self.has_morning_out and (
            self.checkout_start is None or self.checkout_end is None
        ):
            raise ValidationError(
                "Morning checkout times must be set if has_morning_out=True"
            )
        if self.has_afternoon_in and (
            self.afternoon_checkin_start is None or self.afternoon_checkin_end is None
        ):
            raise ValidationError(
                "Afternoon checkin times must be set if has_afternoon_in=True"
            )
        if self.has_afternoon_out and (
            self.afternoon_checkout_start is None or self.afternoon_checkout_end is None
        ):
            raise ValidationError(
                "Afternoon checkout times must be set if has_afternoon_out=True"
            )

    class Meta:
        db_table = "institute_attendance_event_settings"
        # ordering = ["date"]
        # unique_together = ("attendance_event", "date")  # avoid duplicate settings for same event/date
        indexes = [
            models.Index(fields=["event_status", "date"]),
        ]
        constraints = [
            # tanggalon ang constraints here or ilisan og mas better
            # models.UniqueConstraint
            models.UniqueConstraint(
                fields=["institute_attendance_event", "date"],
                name="unique_event_settings_per_date",
            )
        ]

    def __str__(self):
        flags = []

        if self.has_morning_in:
            flags.append("Morning In")
        if self.has_morning_out:
            flags.append("Morning Out")
        if self.has_afternoon_in:
            flags.append("Afternoon In")
        if self.has_afternoon_out:
            flags.append("Afternoon Out")

        flags_display = ", ".join(flags) if flags else "No Checkpoints"

        return (
            f"{self.institute_attendance_event.attendance_event.event_name} "
            f"({self.date}) "
            f"[{flags_display}] "
            f"- {self.get_event_status_display()}"
        )
