import django_filters

from .models import FeeExemptedStudent


class FeeExemptedStudentFilterSet(django_filters.FilterSet):
    """
    Query-param filters for /exempted-fee-students/.

    The Meta block carries every param the list screen already sends
    (`category`, `category__in`, `academic_year`, `semester`, `status`,
    `student`) so nothing deployed breaks; the declared filters add the
    narrowing that screen was missing.
    """

    program_id = django_filters.NumberFilter(field_name="student__program_id")
    program_name = django_filters.CharFilter(
        field_name="student__program__name", lookup_expr="icontains"
    )
    year_level = django_filters.NumberFilter(field_name="student__s_lvl")
    student_school_id = django_filters.CharFilter(
        field_name="student__s_studentID", lookup_expr="icontains"
    )
    reason = django_filters.CharFilter(lookup_expr="icontains")

    # created_at is a datetime; filtering on its date part keeps
    # `?created_lte=2026-08-22` from dropping everything after midnight on
    # the 22nd - same reasoning as FeesFilterSet.due_date.
    created_gte = django_filters.DateFilter(
        field_name="created_at", lookup_expr="date__gte"
    )
    created_lte = django_filters.DateFilter(
        field_name="created_at", lookup_expr="date__lte"
    )

    class Meta:
        model = FeeExemptedStudent
        # `in` lookups take multiple values comma-separated, e.g.
        # `?semester__in=1st,2nd` or `?category__in=3,7` - the same
        # multi-choice convention the audit-log filters follow.
        fields = {
            "category": ["exact", "in"],
            "academic_year": ["exact", "in"],
            "semester": ["exact", "in"],
            "status": ["exact"],
            "student": ["exact"],
        }
