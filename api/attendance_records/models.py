from django.db import models
from core.BaseModel import ActorStampedModel
from api.institutes_attendance_events.models import InstituteAttendanceEvent


class AttendanceRecord(ActorStampedModel):
    student = models.ForeignKey(
        "students.Student", on_delete=models.CASCADE, related_name="attendance_records"
    )
    institute_attendance_event = models.ForeignKey(
        InstituteAttendanceEvent,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    morning_check_in = models.TimeField(null=True)
    morning_check_out = models.TimeField(null=True)
    afternoon_check_in = models.TimeField(null=True)
    afternoon_check_out = models.TimeField(null=True)
    total_fines = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fee = models.ForeignKey(
        "fees.Fee",
        on_delete=models.SET_NULL,
        null=True,
        # blank=True,
        related_name="attendance_records",
    )
    date = models.DateField(db_index=True)

    class Meta:
        db_table = "attendance_records"
        permissions = [
            ("import_attendancerecord", "Can import attendance records from a file"),
            ("bulk_delete_attendancerecord", "Can delete attendance records in bulk"),
        ]
        indexes = [
            models.Index(fields=["student", "date"]),
            models.Index(fields=["institute_attendance_event", "date"]),
            models.Index(fields=["-created_at"]),
            models.Index(fields=["-updated_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "institute_attendance_event", "date"],
                name="unique_student_attendance_records_per_date_in_event",
            )
        ]

    def __str__(self):
        sessions = []

        if self.morning_check_in:
            sessions.append("Morning In")
        if self.morning_check_out:
            sessions.append("Morning Out")
        if self.afternoon_check_in:
            sessions.append("Afternoon In")
        if self.afternoon_check_out:
            sessions.append("Afternoon Out")

        session_display = ", ".join(sessions) if sessions else "No Check-ins"

        return (
            f"{self.student} | "
            # f"{self.institute_attendance_event} | "
            f"{self.date} | "
            f"{session_display} | "
            f"Fine: {self.total_fines}"
        )
