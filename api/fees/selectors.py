"""
Read-side queries for the fees app.

Selectors answer questions; services in `fees/services/` change state. Keeping
them apart means the viewsets stay thin in both directions - a view either
hands a request to a service or hands a queryset to a selector, and never
grows a hundred lines of assembly logic of its own.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable, Optional

from api.attendance_records.models import AttendanceRecord
from api.institutes_attendance_events.models import InstituteAttendanceEvent

from .constants import ATTENDANCE_CATEGORY_NAME
from .models import Fee

NO_RECORDS_MESSAGE = (
    "No attendance records were found for this fee. The maximum attendance "
    "penalty was applied. Please note: non-mandated 'time-in' or 'time-out' "
    "entries are excluded."
)

HAS_RECORDS_MESSAGE = (
    "Please note: non-mandated 'time-in' or 'time-out' entries are excluded. "
    "You will only be penalised for absences or missing logs on dates where "
    "the attendance settings strictly require them."
)


def is_attendance_fee(fee: Fee) -> bool:
    return fee.category.category_name == ATTENDANCE_CATEGORY_NAME


def missed_days(
    *, start: date, end: date, covered: Iterable[date]
) -> list[date]:
    """Every day in [start, end] with no attendance record."""
    seen = set(covered)
    days: list[date] = []
    current = start
    while current <= end:
        if current not in seen:
            days.append(current)
        current += timedelta(days=1)
    return days


def attendance_breakdown(fee: Fee) -> dict[str, Any]:
    """
    Per-day attendance detail behind an attendance fee.

    Backs /fees/{id}/attendance-details/.
    """
    from .serializers import AttendanceRecordReadMiniSerializer

    records = list(
        AttendanceRecord.objects.filter(fee=fee)
        .select_related(
            "institute_attendance_event",
            "institute_attendance_event__attendance_event",
            "updated_by",
        )
        .order_by("date")
    )

    if not records:
        return {
            "event_name": None,
            "attendance_records": [],
            "grand_fee_total": fee.total_amount - fee.discount_total,
            "has_record": False,
            "missed_days": [],
            "message": NO_RECORDS_MESSAGE,
        }

    event = records[0].institute_attendance_event
    return {
        "event_name": event.attendance_event.event_name,
        "attendance_records": AttendanceRecordReadMiniSerializer(
            records, many=True
        ).data,
        "grand_fee_total": fee.total_amount - fee.discount_total,
        "has_record": True,
        "missed_days": missed_days(
            start=event.start_date,
            end=event.end_date,
            covered=(record.date for record in records),
        ),
        "message": HAS_RECORDS_MESSAGE,
    }


def attendance_settings_snapshot(fee: Fee) -> Optional[dict[str, Any]]:
    """
    The event configuration an attendance fee was calculated against.

    Returns None when the fee has no event linked, so the caller can 404
    rather than guess. Backs /fees/{id}/attendance-settings/.
    """
    event = (
        InstituteAttendanceEvent.objects.select_related("attendance_event")
        .prefetch_related("settings")
        .filter(pk=fee.institute_attendance_event_id)
        .first()
    )
    if event is None:
        return None

    return {
        "event": {
            "id": event.pk,
            "name": event.attendance_event.event_name,
            "academic_year": event.academic_year,
            "semester": event.semester,
            "status": event.event_status,
            "date_range": f"{event.start_date} to {event.end_date}",
            "fee_per_absent": fee.category.collection_fee,
        },
        "daily_settings": [
            _daily_setting(setting)
            for setting in sorted(event.settings.all(), key=lambda s: s.date)
        ],
    }


def _daily_setting(setting) -> dict[str, Any]:
    return {
        "date": setting.date,
        "status": setting.event_status,
        "morning": {
            "required_in": setting.has_morning_in,
            "required_out": setting.has_morning_out,
            "in": _window(setting.checkin_start, setting.checkin_end),
            "out": _window(setting.checkout_start, setting.checkout_end),
        },
        "afternoon": {
            "required_in": setting.has_afternoon_in,
            "required_out": setting.has_afternoon_out,
            "in": _window(
                setting.afternoon_checkin_start, setting.afternoon_checkin_end
            ),
            "out": _window(
                setting.afternoon_checkout_start, setting.afternoon_checkout_end
            ),
        },
    }


def _window(start, end) -> Optional[str]:
    return f"{start} - {end}" if start else None
