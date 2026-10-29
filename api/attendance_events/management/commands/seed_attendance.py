"""
Seed the whole attendance chain: events, per-institute runs of them, the
day-by-day settings that say which slots are checked, and student records.

Fees are *not* generated here - that is `seed_fees`, which runs the real
generation service against these records.
"""

from __future__ import annotations

from datetime import date, time, timedelta

from api.attendance_events.models import AttendanceEvent
from api.attendance_records.models import AttendanceRecord
from api.institutes.models import Institute
from api.institutes_attendance_event_settings.models import (
    InstituteAttendanceEventSetting,
)
from api.institutes_attendance_events.models import InstituteAttendanceEvent
from api.seeding.support import SeedCommand
from api.students.models import Student
from core.choices import Semester, get_default_academic_year

EVENTS = [
    ("Foundation Week", "Annual institute foundation celebration."),
    ("General Assembly", "Term-opening assembly for all year levels."),
]

SLOT_TIMES = {
    "checkin_start": time(7, 30),
    "checkin_end": time(8, 30),
    "checkout_start": time(11, 30),
    "checkout_end": time(12, 30),
    "afternoon_checkin_start": time(13, 0),
    "afternoon_checkin_end": time(13, 30),
    "afternoon_checkout_start": time(16, 30),
    "afternoon_checkout_end": time(17, 30),
}

#: Chance a student actually turned up for a given slot.
PRESENT_ODDS = 0.75


class Command(SeedCommand):
    seed_name = "attendance"
    help = "Create demo attendance events, settings and student records."
    depends_on = ("seed_institutes", "seed_students")

    def reset(self, **options):
        AttendanceRecord.objects.all().delete()
        InstituteAttendanceEventSetting.objects.all().delete()
        InstituteAttendanceEvent.objects.all().delete()
        deleted, _ = AttendanceEvent.objects.all().delete()
        self.warn(f"deleted {deleted} attendance event row(s) and their records")

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")
        academic_year = get_default_academic_year()

        events = []
        for name, description in EVENTS:
            event, _ = AttendanceEvent.objects.get_or_create(
                event_name=name, defaults={"description": description}
            )
            events.append(event)

        # Dates in the recent past, so the events read as completed and the
        # records make sense.
        first_day = date.today() - timedelta(days=30)
        days = 2 * self.scale

        created_runs = 0
        created_settings = 0
        created_records = 0

        for institute in institutes:
            for offset, event in enumerate(events):
                start = first_day + timedelta(days=offset * (days + 3))
                run, made = InstituteAttendanceEvent.objects.get_or_create(
                    institute=institute,
                    attendance_event=event,
                    academic_year=academic_year,
                    semester=Semester.FIRST,
                    defaults={
                        "start_date": start,
                        "end_date": start + timedelta(days=days - 1),
                        "event_status": InstituteAttendanceEvent.EventStatus.COMPLETED,
                    },
                )
                created_runs += int(made)

                created_settings += self._seed_settings(run, days)
                created_records += self._seed_records(run, days)

        self.created("institute attendance event", created_runs)
        self.created("event day setting", created_settings)
        self.created("attendance record", created_records)

    def _seed_settings(self, run: InstituteAttendanceEvent, days: int) -> int:
        created = 0
        for offset in range(days):
            day = run.start_date + timedelta(days=offset)
            _, made = InstituteAttendanceEventSetting.objects.get_or_create(
                institute_attendance_event=run,
                date=day,
                defaults={
                    "has_morning_in": True,
                    "has_morning_out": True,
                    "has_afternoon_in": True,
                    "has_afternoon_out": True,
                    "event_status": (
                        InstituteAttendanceEventSetting.EventStatus.COMPLETED
                    ),
                    **SLOT_TIMES,
                },
            )
            created += int(made)
        return created

    def _seed_records(self, run: InstituteAttendanceEvent, days: int) -> int:
        students = list(
            Student.objects.filter(program__institute=run.institute).order_by("id")
        )
        if not students:
            return 0

        created = 0
        for offset in range(days):
            day = run.start_date + timedelta(days=offset)
            for student in students:
                # A missing slot is what the attendance fee charges for, so
                # some students are deliberately left short.
                values = {
                    "morning_check_in": time(7, 45) if self.chance(PRESENT_ODDS) else None,
                    "morning_check_out": time(11, 45) if self.chance(PRESENT_ODDS) else None,
                    "afternoon_check_in": time(13, 10) if self.chance(PRESENT_ODDS) else None,
                    "afternoon_check_out": time(16, 45) if self.chance(PRESENT_ODDS) else None,
                }
                _, made = AttendanceRecord.objects.get_or_create(
                    student=student,
                    institute_attendance_event=run,
                    date=day,
                    defaults=values,
                )
                created += int(made)
        return created
