import django_filters
from .models import InstituteAttendanceEventSetting


class AttendanceEventSettingsFilter(django_filters.FilterSet):
    attendance_event_id = django_filters.NumberFilter(
        field_name="attendance_event__id", lookup_expr="exact"
    )
    date__gte = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date__lte = django_filters.DateFilter(field_name="date", lookup_expr="lte")
    event_status = django_filters.ChoiceFilter(
        choices=InstituteAttendanceEventSetting.EventStatus.choices
    )

    class Meta:
        model = InstituteAttendanceEventSetting
        fields = [
            "attendance_event_id",
            "date__gte",
            "date__lte",
            "date",
            "event_status",
        ]
