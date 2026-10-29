from datetime import date

from django.test import SimpleTestCase

from core.choices import Semester

from ..periods import (
    ReportFilters,
    month_count,
    month_end,
    month_label,
    month_start,
    months_between,
    previous_period,
    shift_academic_year,
)

TODAY = date(2026, 8, 16)


class PreviousPeriodTests(SimpleTestCase):
    def shifted(self, filters: ReportFilters) -> ReportFilters:
        """previous_period(), asserted to have found a comparable window."""
        earlier = previous_period(filters, today=TODAY)
        self.assertIsNotNone(earlier)
        assert earlier is not None  # narrows Optional for the type checker
        return earlier

    def test_date_range_steps_back_by_its_own_length(self):
        filters = ReportFilters(
            date_from=date(2026, 6, 1), date_to=date(2026, 6, 30)
        )

        earlier = self.shifted(filters)

        # 30 days selected, so the 30 days ending the day before it.
        self.assertEqual(earlier.date_from, date(2026, 5, 2))
        self.assertEqual(earlier.date_to, date(2026, 5, 31))

    def test_open_ended_range_runs_to_today(self):
        filters = ReportFilters(date_from=date(2026, 8, 10))

        earlier = self.shifted(filters)

        # 10-16 Aug is seven days, so the seven days before the 10th.
        self.assertEqual(earlier.date_from, date(2026, 8, 3))
        self.assertEqual(earlier.date_to, date(2026, 8, 9))

    def test_range_with_no_start_has_nothing_to_compare(self):
        filters = ReportFilters(date_to=date(2026, 6, 30))

        self.assertIsNone(previous_period(filters, today=TODAY))

    def test_second_semester_compares_with_the_first(self):
        filters = ReportFilters(academic_year="2025-2026", semester=Semester.SECOND)

        earlier = self.shifted(filters)

        self.assertEqual(earlier.academic_year, "2025-2026")
        self.assertEqual(earlier.semester, Semester.FIRST)

    def test_first_semester_compares_with_last_years_second(self):
        filters = ReportFilters(academic_year="2025-2026", semester=Semester.FIRST)

        earlier = self.shifted(filters)

        self.assertEqual(earlier.academic_year, "2024-2025")
        self.assertEqual(earlier.semester, Semester.SECOND)

    def test_academic_year_alone_steps_back_a_year(self):
        filters = ReportFilters(academic_year="2025-2026")

        earlier = self.shifted(filters)

        self.assertEqual(earlier.academic_year, "2024-2025")
        self.assertIsNone(earlier.semester)

    def test_semester_without_a_year_has_nothing_to_compare(self):
        filters = ReportFilters(semester=Semester.FIRST)

        self.assertIsNone(previous_period(filters, today=TODAY))

    def test_no_window_at_all_has_nothing_to_compare(self):
        self.assertIsNone(previous_period(ReportFilters(), today=TODAY))

    def test_other_filters_survive_the_shift(self):
        filters = ReportFilters(
            academic_year="2025-2026",
            semester=Semester.SECOND,
            program_name="BSIT",
            s_set="A",
            compare_previous=True,
        )

        earlier = self.shifted(filters)

        self.assertEqual(earlier.program_name, "BSIT")
        self.assertEqual(earlier.s_set, "A")

    def test_unparseable_academic_year_is_not_guessed_at(self):
        self.assertIsNone(shift_academic_year("whenever", -1))


class MonthHelperTests(SimpleTestCase):
    def test_a_month_runs_from_its_first_day_to_its_last(self):
        day = date(2026, 2, 14)

        self.assertEqual(month_start(day), date(2026, 2, 1))
        self.assertEqual(month_end(day), date(2026, 2, 28))

    def test_february_gains_a_day_in_a_leap_year(self):
        self.assertEqual(month_end(date(2028, 2, 14)), date(2028, 2, 29))

    def test_labels_are_year_then_month_so_they_sort(self):
        self.assertEqual(month_label(date(2026, 2, 1)), "2026-02")
        self.assertEqual(month_label(date(2026, 12, 1)), "2026-12")
        self.assertLess(month_label(date(2026, 2, 1)), month_label(date(2026, 10, 1)))

    def test_counting_months_includes_both_ends(self):
        self.assertEqual(month_count(date(2026, 1, 15), date(2026, 1, 20)), 1)
        self.assertEqual(month_count(date(2026, 1, 31), date(2026, 2, 1)), 2)
        self.assertEqual(month_count(date(2026, 1, 1), date(2026, 12, 31)), 12)
        self.assertEqual(month_count(date(2026, 6, 1), date(2026, 1, 1)), 0)

    def test_listing_months_walks_across_a_year_boundary(self):
        months = months_between(date(2025, 11, 20), date(2026, 2, 3))

        self.assertEqual(
            months,
            [
                date(2025, 11, 1),
                date(2025, 12, 1),
                date(2026, 1, 1),
                date(2026, 2, 1),
            ],
        )

    def test_listing_months_never_skips_a_short_month(self):
        # Stepping by "a month" is easy to get wrong around February.
        months = months_between(date(2026, 1, 31), date(2026, 4, 30))

        self.assertEqual(
            [month_label(m) for m in months],
            ["2026-01", "2026-02", "2026-03", "2026-04"],
        )


class PeriodLabelTests(SimpleTestCase):
    def test_date_range(self):
        filters = ReportFilters(date_from=date(2026, 1, 1), date_to=date(2026, 6, 30))
        self.assertEqual(filters.label, "01 Jan 2026 - 30 Jun 2026")

    def test_academic_year_and_semester(self):
        filters = ReportFilters(academic_year="2025-2026", semester=Semester.FIRST)
        self.assertEqual(filters.label, "First Semester, A.Y. 2025-2026")

    def test_nothing_selected(self):
        self.assertEqual(ReportFilters().label, "All time")

    def test_a_date_range_beats_an_academic_year(self):
        filters = ReportFilters(
            academic_year="2025-2026",
            semester=Semester.FIRST,
            date_from=date(2026, 1, 1),
        )
        self.assertTrue(filters.uses_date_range)
        self.assertEqual(filters.label, "From 01 Jan 2026")
