import csv
from datetime import datetime
from django.db import transaction
from django.core.exceptions import ValidationError

from api.utils.clean import clean_row

from ..models import (
    InstituteAttendanceEvent,
    AttendanceRecord,
)

from api.students.models import Student


class AttendanceImportService:
    def __init__(self, user, file, event_id):
        self.user = user
        self.file = file
        self.event_id = event_id
        self.errors = []
        self.to_create = []
        self.created_summary = []

    def execute(self):
        self._validate_inputs()
        event = self._get_event()
        student_map = self._build_student_map()
        existing_records = self._get_existing_records(event)
        self._process_csv(event, student_map, existing_records)
        self._bulk_create()

        return {
            "totals": {
                "created": len(self.created_summary),
                "errors": len(self.errors),
            },
            "created": self.created_summary,
            "errors": self.errors,
        }

    def _validate_inputs(self):
        if not self.file or not self.event_id:
            raise ValidationError("File and event ID required.")

    def _get_event(self):
        try:
            return InstituteAttendanceEvent.objects.get(
                id=self.event_id, institute=self.user.institute
            )
        except InstituteAttendanceEvent.DoesNotExist:
            raise ValidationError("Invalid event or unauthorized.")

    def _build_student_map(self):
        students = Student.objects.filter(program__institute=self.user.institute)

        student_map = {}
        for s in students:
            student_map[str(s.s_studentID)] = s
            if s.s_rfid:
                student_map[str(s.s_rfid)] = s

        return student_map

    def _get_existing_records(self, event):
        return set(
            AttendanceRecord.objects.filter(institute_attendance_event=event)
            .select_related("student")
            .values_list("student__id", "date")
        )

    def _read_csv(self):
        decoded = self.file.read().decode("utf-8").splitlines()
        return csv.DictReader(decoded)

    def _validate_date(self, raw_date, event, row_index):
        try:
            record_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            self.errors.append(
                {"row": row_index, "error": f"Invalid date format: {raw_date}"}
            )
            return None

        if not (event.start_date <= record_date <= event.end_date):
            self.errors.append(
                {
                    "row": row_index,
                    "error": f"Date {record_date} is outside event range "
                    f"({event.start_date} to {event.end_date})",
                }
            )
            return None

        return record_date

    def _process_csv(self, event, student_map, existing_records):
        reader = self._read_csv()

        seen_in_csv = set()

        required_fields = [
            "morning_check_in",
            "morning_check_out",
            "afternoon_check_in",
            "afternoon_check_out",
            "date",
            "student_school_id_or_rfid",
        ]
        
        stop_the_process = False
        for idx, row in enumerate(reader, start=2):
            row = clean_row(row, ["student_school_id_or_rfid"])
            val = str(row.get("student_school_id_or_rfid"))
            raw_date = row.get("date")

            for field in required_fields:
                if field not in row:
                    self.errors.append(
                        {"row": idx, "error": f"Missing required column: {field}"}
                    )
                    stop_the_process = True
                    break

            if stop_the_process:
                return

            record_date = self._validate_date(raw_date, event, idx)
            if not record_date:
                continue

            student = student_map.get(val)

            if not student:
                self.errors.append({"row": idx, "error": f"Student {val} not found."})
                continue

            key = (student.id, record_date)

            if key in seen_in_csv:
                self.errors.append(
                    {"row": idx, "error": "Duplicate record exists in CSV."}
                )
                continue

            if key in existing_records:
                self.errors.append(
                    {"row": idx, "error": "Duplicate record exists in DB."}
                )
                continue

            seen_in_csv.add(key)

            attendance = AttendanceRecord(
                student=student,
                institute_attendance_event=event,
                date=record_date,
                morning_check_in=row.get("morning_check_in"),
                morning_check_out=row.get("morning_check_out"),
                afternoon_check_in=row.get("afternoon_check_in"),
                afternoon_check_out=row.get("afternoon_check_out"),
                updated_by=self.user,
            )

            self.to_create.append(attendance)

            self.created_summary.append(
                {
                    "student": student.s_studentID,
                    "date": str(record_date),
                }
            )

    def _bulk_create(self):
        if not self.to_create:
            return

        try:
            with transaction.atomic():
                AttendanceRecord.objects.bulk_create(
                    self.to_create, batch_size=500
                )  # ignore_conflicts=True  # will skip any duplicate keys instead of raising
                """
                    In consideration if need mag update instead e reject for creation if existing.
                    AttendanceRecord.objects.bulk_create(
                        to_create,
                        batch_size=500,
                        update_conflicts=True,
                        unique_fields=['student', 'institute_attendance_event', 'date'], # The "UniqueTogether" fields
                        update_fields=[
                            'morning_check_in', 
                            'morning_check_out', 
                            'afternoon_check_in', 
                            'afternoon_check_out',
                            'updated_by'
                        ]
                    )

                    if implemented need to remove validation No "Existing" check.
                """
        except Exception as e:
            self.errors.append({"row": "All", "error": f"Database error: {str(e)}"})
