import django_filters
from django.db.models import Q

from .models import User


class NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter):
    """`?group__in=3,7` - comma-separated ids, the convention the audit and
    exempted-student filters already follow."""


class UserFilterSet(django_filters.FilterSet):
    """
    Query-param filters for /users/.

    Before this existed the viewset only had `filterset_fields`, which meant
    an institute could be narrowed by its *name* but not its id, a school
    only through five `institute__school__*` lookups, and a role not at all.
    The console's account list showed a Role column that could not be
    filtered - the single most-asked-for filter on that screen.

    The `Meta.fields` block keeps every parameter the deployed clients already
    send so nothing breaks; the declared filters below are the additions.

    Group filters are M2M joins, so they carry `distinct=True`: a user who
    matches through two of their groups would otherwise appear twice in the
    page and the paginator's `total_items` would be wrong.
    """

    institute = django_filters.NumberFilter(field_name="institute_id")
    institute_name = django_filters.CharFilter(
        field_name="institute__institute_name", lookup_expr="icontains"
    )
    #: A school administrator's accounts hang off `user.school` directly and
    #: have no institute, so a school filter has to look in both places.
    school = django_filters.NumberFilter(method="filter_school")

    group = django_filters.NumberFilter(field_name="groups__id", distinct=True)
    group__in = NumberInFilter(field_name="groups__id", lookup_expr="in", distinct=True)
    group_name = django_filters.CharFilter(
        field_name="groups__name", lookup_expr="iexact", distinct=True
    )
    #: Accounts with no role at all - the ones an administrator has to fix
    #: before their owner can do anything.
    has_role = django_filters.BooleanFilter(method="filter_has_role")
    has_institute = django_filters.BooleanFilter(
        field_name="institute", lookup_expr="isnull", exclude=True
    )

    # `date_joined` is a datetime; the `date` lookup keeps `joined_to` from
    # dropping every account created after midnight on that day.
    joined_from = django_filters.DateFilter(field_name="date_joined", lookup_expr="date__gte")
    joined_to = django_filters.DateFilter(field_name="date_joined", lookup_expr="date__lte")

    class Meta:
        model = User
        fields = {
            "is_active": ["exact"],
            "is_staff": ["exact"],
            "is_superuser": ["exact"],
            "institute__institute_name": ["exact"],
            "institute__school__id": ["exact"],
            "institute__school__school_name": ["exact"],
            "institute__school__short_name": ["exact"],
            "institute__school__location": ["exact"],
        }

    def filter_school(self, queryset, name, value):
        return queryset.filter(Q(institute__school_id=value) | Q(school_id=value))

    def filter_has_role(self, queryset, name, value):
        if value is None:
            return queryset
        if value:
            return queryset.filter(groups__isnull=False).distinct()
        return queryset.filter(groups__isnull=True)
