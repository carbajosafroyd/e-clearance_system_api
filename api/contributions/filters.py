from django_filters import rest_framework as filters

from core.choices import SystemScope

from .models import Contribution, ContributionKind, FeeDiscount


class ContributionFilterSet(filters.FilterSet):
    system = filters.MultipleChoiceFilter(choices=SystemScope.choices)
    kind = filters.MultipleChoiceFilter(choices=ContributionKind.choices)
    status = filters.MultipleChoiceFilter(choices=Contribution.Status.choices)
    student = filters.NumberFilter(field_name="student_id")
    student_school_id = filters.CharFilter(field_name="student__s_studentID")
    program = filters.NumberFilter(field_name="student__program_id")
    created_from = filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = filters.DateFilter(field_name="created_at", lookup_expr="date__lte")
    min_points = filters.NumberFilter(field_name="points_awarded", lookup_expr="gte")

    class Meta:
        model = Contribution
        fields = ["system", "kind", "impact", "status", "student"]


class FeeDiscountFilterSet(filters.FilterSet):
    system = filters.MultipleChoiceFilter(choices=SystemScope.choices)
    status = filters.MultipleChoiceFilter(choices=FeeDiscount.Status.choices)
    fee = filters.NumberFilter(field_name="fee_id")
    student = filters.NumberFilter(field_name="student_id")
    student_school_id = filters.CharFilter(field_name="student__s_studentID")
    created_from = filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = FeeDiscount
        fields = ["system", "status", "fee", "student"]
