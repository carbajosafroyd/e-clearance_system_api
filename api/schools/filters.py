import django_filters

from .models import School, SchoolBuilding


class SchoolFilterSet(django_filters.FilterSet):
    """
    Query-param filters for /schools/.

    A short list on any deployment, so this is deliberately small: the
    fields a person narrows a campus list by, and nothing speculative.
    """

    school_name = django_filters.CharFilter(lookup_expr="icontains")
    short_name = django_filters.CharFilter(lookup_expr="iexact")
    location = django_filters.CharFilter(lookup_expr="icontains")
    #: Schools that have not been given a logo yet - the one visible sign of
    #: a half-finished setup.
    has_logo = django_filters.BooleanFilter(
        field_name="logo", lookup_expr="isnull", exclude=True
    )

    created_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = School
        fields = ["id"]


class SchoolBuildingFilterSet(django_filters.FilterSet):
    """
    Query-param filters for /school-buildings/.

    `school` by id is the one a superuser needs: the list is scoped to one
    school for everybody else, but a superuser sees every campus's
    buildings in one list and had no way to narrow it.
    """

    school = django_filters.NumberFilter(field_name="school_id")
    school_name = django_filters.CharFilter(field_name="school__school_name", lookup_expr="icontains")
    name = django_filters.CharFilter(lookup_expr="icontains")
    max_floor = django_filters.NumberFilter()
    max_floor_gte = django_filters.NumberFilter(field_name="max_floor", lookup_expr="gte")
    max_floor_lte = django_filters.NumberFilter(field_name="max_floor", lookup_expr="lte")

    class Meta:
        model = SchoolBuilding
        fields = ["id"]
