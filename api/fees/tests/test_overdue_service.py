from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from ..models import Fee
from ..services.overdue_service import mark_fees_as_overdue
from .factories import (
    make_category,
    make_fee,
    make_institute,
    make_program,
    make_staff,
    make_student,
)


class MarkFeesOverdueTests(TestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.category = make_category(self.institute)
        self.student = make_student(self.program)
        self.staff = make_staff(self.institute)

        self.past = timezone.now() - timedelta(days=1)
        self.future = timezone.now() + timedelta(days=1)

    def test_flips_past_due_pending_fees(self):
        fee = make_fee(self.student, self.category, due_date=self.past)

        result = mark_fees_as_overdue(actor=self.staff)

        fee.refresh_from_db()
        self.assertEqual(result.updated_count, 1)
        self.assertEqual(result.fee_ids, [fee.pk])
        self.assertEqual(fee.status, Fee.FeeStatus.OVERDUE)

    def test_leaves_future_and_settled_fees_alone(self):
        upcoming = make_fee(self.student, self.category, due_date=self.future)
        paid = make_fee(
            self.student,
            self.category,
            due_date=self.past,
            status=Fee.FeeStatus.PAID,
        )
        waived = make_fee(
            self.student,
            self.category,
            due_date=self.past,
            status=Fee.FeeStatus.WAIVED,
        )

        result = mark_fees_as_overdue(actor=self.staff)

        self.assertEqual(result.updated_count, 0)
        for fee, expected in (
            (upcoming, Fee.FeeStatus.PENDING),
            (paid, Fee.FeeStatus.PAID),
            (waived, Fee.FeeStatus.WAIVED),
        ):
            fee.refresh_from_db()
            self.assertEqual(fee.status, expected)

    def test_does_not_touch_other_institutes(self):
        """Regression: this used to run across every institute in the database
        for any staff user holding change_fee."""
        other_institute = make_institute()
        other_student = make_student(make_program(other_institute))
        other_category = make_category(other_institute)
        foreign_fee = make_fee(other_student, other_category, due_date=self.past)
        own_fee = make_fee(self.student, self.category, due_date=self.past)

        result = mark_fees_as_overdue(actor=self.staff)

        own_fee.refresh_from_db()
        foreign_fee.refresh_from_db()
        self.assertEqual(result.fee_ids, [own_fee.pk])
        self.assertEqual(own_fee.status, Fee.FeeStatus.OVERDUE)
        self.assertEqual(foreign_fee.status, Fee.FeeStatus.PENDING)

    def test_superuser_spans_institutes(self):
        other_institute = make_institute()
        other_student = make_student(make_program(other_institute))
        foreign_fee = make_fee(
            other_student, make_category(other_institute), due_date=self.past
        )
        own_fee = make_fee(self.student, self.category, due_date=self.past)

        result = mark_fees_as_overdue(actor=make_staff(None, superuser=True))

        self.assertEqual(result.updated_count, 2)
        self.assertCountEqual(result.fee_ids, [own_fee.pk, foreign_fee.pk])

    def test_staff_without_institute_changes_nothing(self):
        make_fee(self.student, self.category, due_date=self.past)

        result = mark_fees_as_overdue(actor=make_staff(None))

        self.assertEqual(result.updated_count, 0)
        self.assertEqual(
            Fee.objects.filter(status=Fee.FeeStatus.OVERDUE).count(), 0
        )

    def test_bumps_updated_at(self):
        """`.update()` skips auto_now, and both Fee.Meta.ordering and the
        viewset sort on updated_at - a stale value buries overdue fees."""
        fee = make_fee(self.student, self.category, due_date=self.past)
        Fee.objects.filter(pk=fee.pk).update(
            updated_at=timezone.now() - timedelta(days=30)
        )
        before = Fee.objects.get(pk=fee.pk).updated_at

        mark_fees_as_overdue(actor=self.staff)

        self.assertGreater(Fee.objects.get(pk=fee.pk).updated_at, before)

    def test_records_the_actor(self):
        fee = make_fee(self.student, self.category, due_date=self.past)

        mark_fees_as_overdue(actor=self.staff)

        fee.refresh_from_db()
        self.assertEqual(fee.updated_by, self.staff)
