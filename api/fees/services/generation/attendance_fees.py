"""Attendance fine generation, billed per missed required period."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from api.attendance_records.models import AttendanceRecord
from api.collection_categories.models import CollectionCategory
from api.institutes_attendance_events.models import InstituteAttendanceEvent

from ...constants import BULK_CREATE_BATCH_SIZE
from ...models import Fee
from .strategy import FeeGenerationStrategy

#: (setting flag, attendance column). A flag that is False means the period is
#: optional that day, so nothing is charged for it regardless of attendance.
PERIODS: tuple[tuple[str, str], ...] = (
    ("has_morning_in", "morning_check_in"),
    ("has_morning_out", "morning_check_out"),
    ("has_afternoon_in", "afternoon_check_in"),
    ("has_afternoon_out", "afternoon_check_out"),
)


class AttendanceFeeGeneration(FeeGenerationStrategy):
    """
    One fee per student per attendance event, summing the collection fee over
    every required period the student has no timestamp for.

    Scope is the event, not the category: an institute runs several attendance
    events per term and each is billed separately, which is why the category +
    academic year + semester key used by the general flow does not fit here.
    """

    skip_zero_amount = True

    def __init__(
        self,
        *,
        category: CollectionCategory,
        event: InstituteAttendanceEvent,
    ) -> None:
        self.category = category
        self.event = event
        self.academic_year = event.academic_year
        self.semester = event.semester

        self._settings_by_date: dict[date, object] = {}
        self._records_by_student: dict[int, dict[date, AttendanceRecord]] = {}
        self._touched_records: list[AttendanceRecord] = []

    def prefetch(self, student_ids: Sequence[int]) -> None:
        self._settings_by_date = {
            setting.date: setting for setting in self.event.settings.all()
        }

        records = AttendanceRecord.objects.filter(
            student_id__in=student_ids,
            institute_attendance_event=self.event,
        )
        for record in records:
            record_date = (
                record.date if isinstance(record.date, date) else record.date.date()
            )
            self._records_by_student.setdefault(record.student_id, {})[
                record_date
            ] = record

    def existing_fee_student_ids(self, student_ids: Sequence[int]) -> set[int]:
        return set(
            Fee.objects.filter(
                student_id__in=student_ids,
                institute_attendance_event=self.event,
            ).values_list("student_id", flat=True)
        )

    def amount_for(self, student_id: int) -> Decimal:
        records = self._records_by_student.get(student_id, {})
        fee_per_period = self.category.collection_fee

        # Recompute from scratch: a record's stored total_fines may be left
        # over from an earlier run against different settings.
        for record in records.values():
            record.total_fines = Decimal("0")

        total = Decimal("0")
        current = self.event.start_date
        while current <= self.event.end_date:
            setting = self._settings_by_date.get(current)
            if setting is None:
                # No settings row for this day, so nothing was required.
                current += timedelta(days=1)
                continue

            record = records.get(current)
            for setting_flag, record_column in PERIODS:
                if not getattr(setting, setting_flag, False):
                    continue
                if record is not None and getattr(record, record_column, None):
                    continue

                total += fee_per_period
                if record is not None:
                    record.total_fines += fee_per_period

            current += timedelta(days=1)

        self._touched_records.extend(records.values())
        return total

    def fee_kwargs(self) -> dict:
        return {"institute_attendance_event": self.event}

    def lock_scope(self) -> None:
        list(
            InstituteAttendanceEvent.objects.select_for_update().filter(
                pk=self.event.pk
            )
        )

    def on_persisted(self, fees: Sequence[Fee]) -> None:
        """Point each attendance record at the fee it was billed into, and
        write back the per-day fine we just computed."""
        fee_id_by_student = {fee.student_id: fee.pk for fee in fees}

        to_update = []
        for record in self._touched_records:
            fee_id = fee_id_by_student.get(record.student_id)
            if fee_id is None:
                continue
            record.fee_id = fee_id
            to_update.append(record)

        if to_update:
            AttendanceRecord.objects.bulk_update(
                to_update, ["fee", "total_fines"], batch_size=BULK_CREATE_BATCH_SIZE
            )
