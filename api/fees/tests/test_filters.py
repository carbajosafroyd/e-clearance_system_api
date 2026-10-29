from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from ..filters import FeesFilterSet
from ..models import Fee
from .factories import make_category, make_fee, make_institute, make_program, make_student


class DueDateFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.category = make_category(cls.institute)
        cls.student = make_student(cls.program)

    def filtered(self, **params):
        return FeesFilterSet(params, queryset=Fee.objects.all()).qs

    def test_due_date_filters_execute(self):
        """Regression: these were declared against a `date` column that does
        not exist on Fee, so every due-date query raised FieldError."""
        make_fee(self.student, self.category)
        today = timezone.localdate().isoformat()

        for params in (
            {"due_date": today},
            {"due_date_gte": today},
            {"due_date_lte": today},
        ):
            with self.subTest(params=params):
                list(self.filtered(**params))  # must not raise

    def test_due_date_lte_covers_the_whole_day(self):
        """A fee due late on day X must still match `due_date_lte=X` - the
        point of filtering on the date part rather than the raw datetime."""
        due = timezone.now().replace(hour=23, minute=30, second=0, microsecond=0)
        fee = make_fee(self.student, self.category, due_date=due)

        matched = self.filtered(due_date_lte=timezone.localdate().isoformat())

        self.assertIn(fee, matched)

    def test_due_date_gte_excludes_earlier_days(self):
        make_fee(
            self.student,
            self.category,
            due_date=timezone.now() - timedelta(days=3),
        )
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()

        self.assertEqual(self.filtered(due_date_gte=tomorrow).count(), 0)


class ScopeFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.category = make_category(cls.institute, name="Uniform")
        cls.student = make_student(cls.program, level=2)
        cls.fee = make_fee(cls.student, cls.category, amount="250.00")

    def filtered(self, **params):
        return FeesFilterSet(params, queryset=Fee.objects.all()).qs

    def test_amount_and_relation_filters_execute(self):
        cases = [
            {"total_amount": "250.00"},
            {"total_amount_gte": "100"},
            {"total_amount_lte": "500"},
            {"balance_gte": "1"},
            {"category_name": "unif"},
            {"institute_name": self.institute.institute_name[:5]},
            {"program_name": self.program.name[:5]},
            {"year_level": 2},
            {"student_school_id": self.student.s_studentID},
        ]
        for params in cases:
            with self.subTest(params=params):
                self.assertIn(self.fee, self.filtered(**params))
