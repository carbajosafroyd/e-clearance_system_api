import django_filters

from .models import Program


class NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter):
    """`?institute__in=1,4` - comma-separated ids."""


class ProgramFilterSet(django_filters.FilterSet):
    """
    Query-param filters for /programs/.

    The old `filterset_fields` matched the program `name` EXACTLY and the
    institute only by its name, so the console's program list could not be
    narrowed to an institute by id - the thing it actually holds - and a
    search for "Information" found nothing because the name is "BS
    Information Technology".

    `Meta.fields` keeps the parameters the deployed clients send; the
    declared filters add ids, `in` lookups and case-insensitive matching.
    """

    institute = django_filters.NumberFilter(field_name="institute_id")
    institute__in = NumberInFilter(field_name="institute_id", lookup_expr="in")
    institute_name = django_filters.CharFilter(
        field_name="institute__institute_name", lookup_expr="icontains"
    )
    school = django_filters.NumberFilter(field_name="institute__school_id")
    name_contains = django_filters.CharFilter(field_name="name", lookup_expr="icontains")
    acronym = django_filters.CharFilter(lookup_expr="iexact")
    status__in = django_filters.BaseInFilter(field_name="status", lookup_expr="in")

    class Meta:
        model = Program
        fields = {
            "status": ["exact"],
            "name": ["exact"],
            "institute__institute_name": ["exact"],
            "institute__school__id": ["exact"],
            "institute__school__school_name": ["exact"],
            "institute__school__short_name": ["exact"],
        }
