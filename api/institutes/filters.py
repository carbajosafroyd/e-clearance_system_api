import django_filters

from .models import Institute


class InstituteFilterSet(django_filters.FilterSet):
    """
    Query-param filters for /institutes/.

    This file was empty and the viewset had no `filterset_fields`, so the
    only way to narrow the list was the search box - which also matches the
    school's name and id, and therefore cannot answer "the institutes of
    school 3" without also returning any institute whose name happens to
    contain a 3. A superuser administering several schools needs the id.
    """

    school = django_filters.NumberFilter(field_name="school_id")
    school_name = django_filters.CharFilter(field_name="school__school_name", lookup_expr="icontains")
    institute_name = django_filters.CharFilter(lookup_expr="icontains")
    acronym = django_filters.CharFilter(lookup_expr="iexact")
    has_logo = django_filters.BooleanFilter(
        field_name="logo", lookup_expr="isnull", exclude=True
    )

    created_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = Institute
        fields = ["id"]
