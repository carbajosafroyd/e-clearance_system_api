from django_filters import rest_framework as filters

from core.choices import SystemScope

from .models import Complaint, ComplaintCategory


class ComplaintFilterSet(filters.FilterSet):
    #: Jump straight back to one record. A complaint is referred to by its
    #: number once it has been discussed with a student ("about #482"), so
    #: looking that number up has to be a filter and not a text search -
    #: searching "482" would also hit every complaint whose text mentions it.
    id = filters.NumberFilter(field_name="id")
    system = filters.MultipleChoiceFilter(choices=SystemScope.choices)
    category = filters.MultipleChoiceFilter(choices=ComplaintCategory.choices)
    status = filters.MultipleChoiceFilter(choices=Complaint.Status.choices)
    student = filters.NumberFilter(field_name="student_id")
    student_school_id = filters.CharFilter(field_name="student__s_studentID")
    program = filters.NumberFilter(field_name="student__program_id")
    fee = filters.NumberFilter(field_name="related_fee_id")
    #: The default queue view: everything nobody has answered yet.
    is_open = filters.BooleanFilter(method="filter_is_open")
    created_from = filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = Complaint
        fields = ["id", "system", "category", "status", "student", "fee"]

    def filter_is_open(self, queryset, name, value):
        if value is None:
            return queryset
        lookup = {"status__in": Complaint.OPEN_STATUSES}
        return queryset.filter(**lookup) if value else queryset.exclude(**lookup)
