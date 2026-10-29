from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from api.attendance_records.models import AttendanceRecord
from api.students.models import Student

from ..models import Fee, GeneratedFeeBatch
from ..services.generation import GenerationRequest
from ..services.generation.runner import (
    run_attendance_generation,
    run_general_generation,
)
from .factories import (
    ACADEMIC_YEAR,
    SEMESTER,
    make_attendance_category,
    make_category,
    make_event,
    make_exemption,
    make_institute,
    make_program,
    make_record,
    make_setting,
    make_staff,
    make_student,
)


def a_request(**overrides) -> GenerationRequest:
    defaults = {
        "due_date": timezone.now() + timedelta(days=30),
        "all_students": True,
        "year_levels": (),
        "apply_exemptions": False,
    }
    defaults.update(overrides)
    return GenerationRequest(**defaults)


class GeneralFeeGenerationTests(TestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.staff = make_staff(self.institute)
        self.category = make_category(self.institute, name="Uniform", fee="300.00")

    def generate(self, **overrides):
        run_general_generation(
            user_id=self.staff.pk,
            category_id=self.category.pk,
            academic_year=ACADEMIC_YEAR,
            semester=SEMESTER,
            request=a_request(**overrides),
        )

    def test_bills_every_enrolled_student(self):
        make_student(self.program)
        make_student(self.program)

        self.generate()

        fees = Fee.objects.all()
        self.assertEqual(fees.count(), 2)
        for fee in fees:
            self.assertEqual(fee.total_amount, Decimal("300.00"))
            self.assertEqual(fee.balance, fee.total_amount)
            self.assertEqual(fee.status, Fee.FeeStatus.PENDING)
            self.assertEqual(fee.issued_by, self.staff)

    def test_records_the_batch(self):
        make_student(self.program)

        self.generate()

        batch = GeneratedFeeBatch.objects.get()
        self.assertEqual(batch.total_created_fees, 1)
        self.assertEqual(batch.institute, self.institute)
        self.assertEqual(batch.generated_by, self.staff)
        self.assertEqual(Fee.objects.get().gen_fee_batch, batch)

    def test_skips_non_enrolled_students(self):
        make_student(self.program, status=Student.StudentStatus.GRADUATED)
        make_student(self.program, status=Student.StudentStatus.DROPPED)

        self.generate()

        self.assertEqual(Fee.objects.count(), 0)
        self.assertEqual(GeneratedFeeBatch.objects.count(), 0)

    def test_honours_year_level_filter(self):
        first_year = make_student(self.program, level=1)
        make_student(self.program, level=3)

        self.generate(all_students=False, year_levels=(1,))

        self.assertEqual(
            list(Fee.objects.values_list("student_id", flat=True)), [first_year.pk]
        )

    def test_does_not_bill_twice(self):
        make_student(self.program)

        self.generate()
        self.generate()

        self.assertEqual(Fee.objects.count(), 1)
        # The second run persisted nothing, so its empty batch is cleaned up.
        self.assertEqual(GeneratedFeeBatch.objects.count(), 1)

    def test_waives_exempted_students_when_asked(self):
        exempted = make_student(self.program)
        billed = make_student(self.program)
        make_exemption(exempted, self.category, reason="Working scholar")

        self.generate(apply_exemptions=True)

        waived_fee = Fee.objects.get(student=exempted)
        self.assertEqual(waived_fee.status, Fee.FeeStatus.WAIVED)
        self.assertEqual(waived_fee.remarks, "Working scholar")
        self.assertEqual(
            Fee.objects.get(student=billed).status, Fee.FeeStatus.PENDING
        )

    def test_bills_exempted_students_when_not_asked(self):
        exempted = make_student(self.program)
        make_exemption(exempted, self.category)

        self.generate(apply_exemptions=False)

        self.assertEqual(
            Fee.objects.get(student=exempted).status, Fee.FeeStatus.PENDING
        )

    def test_ignores_students_from_other_institutes(self):
        other_program = make_program(make_institute())
        make_student(other_program)
        mine = make_student(self.program)

        self.generate()

        self.assertEqual(
            list(Fee.objects.values_list("student_id", flat=True)), [mine.pk]
        )


class AttendanceFeeGenerationTests(TestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.staff = make_staff(self.institute)
        self.category = make_attendance_category(self.institute, fee="50.00")
        self.event = make_event(self.institute, days=1)
        self.day = self.event.start_date

    def generate(self, **overrides):
        run_attendance_generation(
            user_id=self.staff.pk,
            institute_attendance_event_id=self.event.pk,
            request=a_request(**overrides),
        )

    def test_no_fee_for_a_student_who_owes_nothing(self):
        """Regression: perfect attendance produced a 0.00 PENDING fee, which
        the student was notified about and which later fell OVERDUE."""
        make_setting(self.event, self.day, has_morning_in=True)
        present = make_student(self.program)
        make_record(present, self.event, self.day, morning_check_in=time(8, 0))

        self.generate()

        self.assertFalse(Fee.objects.filter(student=present).exists())

    def test_charges_each_missed_required_period(self):
        make_setting(
            self.event, self.day, has_morning_in=True, has_afternoon_in=True
        )
        absent = make_student(self.program)

        self.generate()

        fee = Fee.objects.get(student=absent)
        self.assertEqual(fee.total_amount, Decimal("100.00"))  # two periods
        self.assertEqual(fee.balance, fee.total_amount)
        self.assertEqual(fee.institute_attendance_event, self.event)

    def test_charges_only_the_period_that_was_missed(self):
        make_setting(
            self.event, self.day, has_morning_in=True, has_morning_out=True
        )
        student = make_student(self.program)
        make_record(student, self.event, self.day, morning_check_in=time(8, 0))

        self.generate()

        self.assertEqual(Fee.objects.get(student=student).total_amount, Decimal("50.00"))

    def test_optional_periods_are_never_charged(self):
        make_setting(self.event, self.day)  # every flag False
        make_student(self.program)

        self.generate()

        self.assertEqual(Fee.objects.count(), 0)

    def test_days_without_settings_are_not_charged(self):
        # Event spans a day, but nobody configured what it required.
        make_student(self.program)

        self.generate()

        self.assertEqual(Fee.objects.count(), 0)

    def test_links_records_to_the_fee_it_was_billed_into(self):
        make_setting(
            self.event, self.day, has_morning_in=True, has_morning_out=True
        )
        student = make_student(self.program)
        record = make_record(
            student, self.event, self.day, morning_check_in=time(8, 0)
        )

        self.generate()

        fee = Fee.objects.get(student=student)
        record.refresh_from_db()
        self.assertEqual(record.fee, fee)
        self.assertEqual(record.total_fines, Decimal("50.00"))

    def test_does_not_bill_the_same_event_twice(self):
        make_setting(self.event, self.day, has_morning_in=True)
        make_student(self.program)

        self.generate()
        self.generate()

        self.assertEqual(Fee.objects.count(), 1)

    def test_a_second_event_bills_separately(self):
        """Attendance is scoped per event, not per category+term - a student
        can legitimately owe for two events in the same semester."""
        make_setting(self.event, self.day, has_morning_in=True)
        student = make_student(self.program)
        self.generate()

        self.event = make_event(self.institute, days=1)
        self.day = self.event.start_date
        make_setting(self.event, self.day, has_morning_in=True)
        self.generate()

        self.assertEqual(Fee.objects.filter(student=student).count(), 2)

    def test_waives_exempted_students_when_asked(self):
        make_setting(self.event, self.day, has_morning_in=True)
        exempted = make_student(self.program)
        make_exemption(exempted, self.category, reason="Medical")

        self.generate(apply_exemptions=True)

        fee = Fee.objects.get(student=exempted)
        self.assertEqual(fee.status, Fee.FeeStatus.WAIVED)
        self.assertEqual(fee.remarks, "Medical")
        # A waived fee still carries what would have been owed.
        self.assertEqual(fee.total_amount, Decimal("50.00"))

    def test_exempt_student_with_perfect_attendance_gets_no_row(self):
        make_setting(self.event, self.day, has_morning_in=True)
        exempted = make_student(self.program)
        make_exemption(exempted, self.category)
        make_record(exempted, self.event, self.day, morning_check_in=time(8, 0))

        self.generate(apply_exemptions=True)

        self.assertFalse(Fee.objects.filter(student=exempted).exists())

    def test_recomputes_stale_fines(self):
        make_setting(self.event, self.day, has_morning_in=True)
        student = make_student(self.program)
        record = make_record(student, self.event, self.day)
        AttendanceRecord.objects.filter(pk=record.pk).update(
            total_fines=Decimal("999.00")
        )

        self.generate()

        record.refresh_from_db()
        self.assertEqual(record.total_fines, Decimal("50.00"))
