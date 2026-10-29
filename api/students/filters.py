import django_filters

from .models import Student


class NumberInFilter(django_filters.BaseInFilter, django_filters.NumberFilter):
    """`?program__in=3,7` - comma-separated, the convention the audit and
    exempted-student filters follow."""


class CharInFilter(django_filters.BaseInFilter, django_filters.CharFilter):
    """`?s_status__in=enrolled,to_be_updated`."""


class StudentFilterSet(django_filters.FilterSet):
    """
    Query-param filters for /students/.

    The viewset used to expose `filterset_fields` only: status, level,
    section, and the program by NAME. The name lookup was the problem. The
    console's filter dialog has the program *id* in hand (it renders the
    reference list), and had to round-trip through the name - so two
    programs called "BS Information Technology" in two institutes could not
    be told apart, and a school administrator could not narrow to one
    institute at all.

    `Meta.fields` keeps every parameter the deployed clients already send.
    The declared filters are the additions: ids where there were names, `in`
    lookups for multi-select, and `has_account` for the question every
    invitation round starts with - "who still has no account?".
    """

    program = django_filters.NumberFilter(field_name="program_id")
    program__in = NumberInFilter(field_name="program_id", lookup_expr="in")
    program_name = django_filters.CharFilter(field_name="program__name", lookup_expr="icontains")
    institute = django_filters.NumberFilter(field_name="program__institute_id")
    institute__in = NumberInFilter(field_name="program__institute_id", lookup_expr="in")
    school = django_filters.NumberFilter(field_name="program__institute__school_id")

    s_status__in = CharInFilter(field_name="s_status", lookup_expr="in")
    s_lvl__in = NumberInFilter(field_name="s_lvl", lookup_expr="in")
    # The console's section filter is typed, and "a" should find section
    # "A"; the exact `s_set` in Meta stays for the callers that rely on it.
    section = django_filters.CharFilter(field_name="s_set", lookup_expr="iexact")

    #: True -> students with a linked account, False -> the ones still to
    #: invite. `exclude=True` flips `isnull` so the parameter reads the way
    #: a person would ask the question.
    has_account = django_filters.BooleanFilter(
        field_name="user", lookup_expr="isnull", exclude=True
    )
    has_rfid = django_filters.BooleanFilter(
        field_name="s_rfid", lookup_expr="isnull", exclude=True
    )

    # created_at is a datetime; the `date` lookup keeps `created_to` from
    # dropping everyone added after midnight on that day.
    created_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")

    class Meta:
        model = Student
        fields = {
            "s_set": ["exact"],
            "s_lvl": ["exact", "gte", "lte"],
            "s_status": ["exact"],
            "s_rfid": ["exact"],
            "s_studentID": ["exact"],
            "program__name": ["exact"],
            "program__acronym": ["exact"],
        }
