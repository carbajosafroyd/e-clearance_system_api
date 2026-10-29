"""Minimal object builders for the fees tests.

Plain functions rather than a factory library - the app has no test
dependencies today and these only need to be good enough to exercise the
fee rules.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from api.attendance_events.models import AttendanceEvent
from api.attendance_records.models import AttendanceRecord
from api.collection_categories.models import CollectionCategory, FeeExemptedStudent
from api.institutes.models import Institute
from api.institutes_attendance_event_settings.models import (
    InstituteAttendanceEventSetting,
)
from api.institutes_attendance_events.models import InstituteAttendanceEvent
from api.programs.models import Program
from api.rbac.roles import Roles
from api.rbac.services import assign_role
from api.schools.models import School
from api.students.models import Student
from api.users.models import User
from core.choices import Semester, get_default_academic_year

from ..constants import ATTENDANCE_CATEGORY_NAME
from ..models import Fee

ACADEMIC_YEAR = get_default_academic_year()
SEMESTER = Semester.FIRST

_seq = itertools.count(1)


def make_institute(name: str | None = None) -> Institute:
    n = next(_seq)
    school = School.objects.create(school_name=f"School {n}", location="Davao")
    return Institute.objects.create(
        institute_name=name or f"Institute {n}", school=school
    )


def make_program(institute: Institute) -> Program:
    n = next(_seq)
    return Program.objects.create(
        name=f"Program {n}", acronym=f"P{n}", institute=institute
    )


def make_staff(
    institute: Institute | None,
    *,
    superuser: bool = False,
    role: str | None = Roles.CMS_ADMIN,
) -> User:
    """A back-office user, in a real role.

    Endpoints are gated on permissions (see `api/rbac/`), so a bare
    ``is_staff=True`` user is refused everywhere. Putting the account in an
    actual role also means the tests exercise the same grants production
    runs on - `roles.py` losing a permission shows up here as a failure.
    Pass ``role=None`` for the deliberately-unauthorised case.
    """
    n = next(_seq)
    user = User.objects.create_user(
        username=f"staff{n}",
        password="pw",
        institute=institute,
        is_staff=True,
        is_superuser=superuser,
    )
    if role and not superuser:
        assign_role(user, role)
    return user


def make_student(
    program: Program,
    *,
    level: int = 1,
    status: str = Student.StudentStatus.ENROLLED,
    with_user: bool = True,
) -> Student:
    n = next(_seq)
    user = None
    if with_user:
        user = User.objects.create_user(
            username=f"student{n}", password="pw", institute=program.institute
        )
        assign_role(user, Roles.STUDENT)
    return Student.objects.create(
        s_studentID=f"{n:010d}",
        program=program,
        user=user,
        s_fname="Test",
        s_lname=f"Student{n}",
        s_lvl=level,
        s_status=status,
    )


def make_category(
    institute: Institute, *, name: str | None = None, fee: str = "100.00"
) -> CollectionCategory:
    n = next(_seq)
    return CollectionCategory.objects.create(
        institute=institute,
        category_name=name or f"Category {n}",
        collection_fee=Decimal(fee),
    )


def make_attendance_category(
    institute: Institute, *, fee: str = "50.00"
) -> CollectionCategory:
    return make_category(institute, name=ATTENDANCE_CATEGORY_NAME, fee=fee)


def make_exemption(
    student: Student, category: CollectionCategory, *, reason: str = "Scholar"
) -> FeeExemptedStudent:
    return FeeExemptedStudent.objects.create(
        student=student,
        category=category,
        reason=reason,
        academic_year=ACADEMIC_YEAR,
        semester=SEMESTER,
        status=True,
    )


def make_event(
    institute: Institute,
    *,
    start: date | None = None,
    days: int = 1,
) -> InstituteAttendanceEvent:
    n = next(_seq)
    start = start or date(2026, 3, 2)
    event = AttendanceEvent.objects.create(event_name=f"Event {n}")
    return InstituteAttendanceEvent.objects.create(
        institute=institute,
        attendance_event=event,
        academic_year=ACADEMIC_YEAR,
        semester=SEMESTER,
        start_date=start,
        end_date=start + timedelta(days=days - 1),
    )


def make_setting(
    event: InstituteAttendanceEvent, day: date, **flags
) -> InstituteAttendanceEventSetting:
    return InstituteAttendanceEventSetting.objects.create(
        institute_attendance_event=event,
        date=day,
        has_morning_in=flags.get("has_morning_in", False),
        has_morning_out=flags.get("has_morning_out", False),
        has_afternoon_in=flags.get("has_afternoon_in", False),
        has_afternoon_out=flags.get("has_afternoon_out", False),
    )


def make_record(
    student: Student, event: InstituteAttendanceEvent, day: date, **times
) -> AttendanceRecord:
    return AttendanceRecord.objects.create(
        student=student,
        institute_attendance_event=event,
        date=day,
        morning_check_in=times.get("morning_check_in"),
        morning_check_out=times.get("morning_check_out"),
        afternoon_check_in=times.get("afternoon_check_in"),
        afternoon_check_out=times.get("afternoon_check_out"),
    )


def make_fee(
    student: Student,
    category: CollectionCategory,
    *,
    amount: str = "100.00",
    status: str = Fee.FeeStatus.PENDING,
    due_date=None,
    **extra,
) -> Fee:
    value = Decimal(amount)
    return Fee.objects.create(
        student=student,
        category=category,
        total_amount=value,
        balance=value,
        status=status,
        due_date=due_date if due_date is not None else timezone.now() + timedelta(days=7),
        academic_year=ACADEMIC_YEAR,
        semester=SEMESTER,
        **extra,
    )
