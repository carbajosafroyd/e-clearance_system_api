from core.BaseModel import ActorStampedModel
from django.db import models
from api.institutes.models import Institute
from api.attendance_events.models import AttendanceEvent
from core.choices import AcademicYear, Semester, get_default_academic_year
from api.students.models import Student


class InstituteAttendanceEvent(ActorStampedModel):
    class EventStatus(models.TextChoices):
        UPCOMING = "upcoming", "Upcoming"
        ONGOING = "ongoing", "Ongoing"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    institute = models.ForeignKey(
        Institute, on_delete=models.CASCADE, related_name="institute_attendance_events"
    )
    # students = models.ManyToManyField(
    #     Student, through="attendance_records.AttendanceRecord"
    # )  # para ni for viewing sa mga students nga naa ana nga event :>
    attendance_event = models.ForeignKey(
        AttendanceEvent,
        on_delete=models.CASCADE,   
        related_name="institute_attendance_events",
    )

    academic_year = models.CharField(
        max_length=9, choices=AcademicYear.choices, default=get_default_academic_year
    )
    semester = models.CharField(
        max_length=15, choices=Semester.choices, default=Semester.FIRST
    )
    start_date = models.DateField(null=False, blank=False)
    end_date = models.DateField(null=False, blank=False)
    event_status = models.CharField(
        max_length=15, choices=EventStatus.choices, default=EventStatus.UPCOMING, db_index=True
    )

    class Meta:
        db_table = "institute_attendance_events"
        indexes = [
            models.Index(fields=["academic_year", "semester"]),
        ]
    def __str__(self):
        return f"IAE: {self.id} - {self.attendance_event.event_name}: {self.start_date}-{self.end_date} {self.academic_year}/{self.semester} semester."
