import django_filters
from django.db.models import Q

from core import features, setting_buckets
from core.choices import SystemScope

from .models import SystemSetting


class SystemSettingFilterSet(django_filters.FilterSet):
    """
    Query-param filters for `/system-settings/`.

    Until this existed the only narrowing was `?system=`, which is what the
    other frontends need (their own rows) and all a one-institute account
    ever needed. The console reads across institutes - a superuser's list is
    every institute on the platform, a school administrator's every one in
    the school - and a page of that has to be narrowable by where a row
    belongs and what kind of setting it is.
    """

    institute = django_filters.NumberFilter(field_name="institute_id")
    school = django_filters.NumberFilter(field_name="institute__school_id")
    #: One system's rows PLUS the platform-wide ones: the academic year is
    #: not the collection app's setting, it is the institute's, and a
    #: frontend asking for its own settings would otherwise need a second
    #: request for the two rows it cannot run without. A plain exact filter
    #: here silently dropped them.
    system = django_filters.ChoiceFilter(choices=SystemScope.choices, method="filter_system")
    #: The permission bucket a key belongs to - the sections the console's
    #: form is split into. Resolved through `setting_buckets`, so a key
    #: bucketed there is filterable here without a second list.
    bucket = django_filters.ChoiceFilter(
        choices=[(bucket, bucket) for bucket in setting_buckets.ALL_BUCKETS],
        method="filter_bucket",
    )
    setting_key = django_filters.CharFilter(lookup_expr="iexact")
    key = django_filters.CharFilter(field_name="setting_key", lookup_expr="icontains")
    changed_from = django_filters.DateFilter(field_name="updated_at", lookup_expr="date__gte")
    changed_to = django_filters.DateFilter(field_name="updated_at", lookup_expr="date__lte")

    class Meta:
        model = SystemSetting
        fields = ["id"]

    def filter_system(self, queryset, name, value):
        return queryset.filter(system__in=(value, SystemScope.PLATFORM))

    def filter_bucket(self, queryset, name, value):
        keys = list(setting_buckets.keys_in(value))
        if value == setting_buckets.FEATURES:
            # Feature rows are recognised by prefix, not listed in the
            # catalog - see `setting_buckets.permission_for`.
            return queryset.filter(
                Q(setting_key__in=keys) | Q(setting_key__startswith=features.SETTING_PREFIX)
            )
        return queryset.filter(setting_key__in=keys)
