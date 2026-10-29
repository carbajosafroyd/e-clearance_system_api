import django_filters
from .models import AttendanceEvent

class AttendanceEventFilter(django_filters.FilterSet):

    # start_date__gte = django_filters.DateTimeFilter(field_name="start_date", lookup_expr="gte")
    # start_date__lte = django_filters.DateTimeFilter(field_name="start_date", lookup_expr="lte")
    # end_date__gte = django_filters.DateTimeFilter(field_name="end_date", lookup_expr="gte")
    # end_date__lte = django_filters.DateTimeFilter(field_name="end_date", lookup_expr="lte")
    date_range = django_filters.IsoDateTimeFromToRangeFilter(
        field_name='start_date',
        method='filter_overlapping'
    )
    school_name = django_filters.CharFilter(field_name="school__school_name", lookup_expr="icontains")
    
    class Meta:
        model = AttendanceEvent
        fields = [
            "event_status",
        ]

    def filter_overlapping(self, queryset, name, value):
        start = value.start
        end = value.stop
        if start and end:
            # return events that overlap the range
            return queryset.filter(start_date__lt=end, end_date__gt=start)
        return queryset
    
        """sample use
        /api/v1/attendance-events/?date_range_after=2025-12-10T00:00:00Z&date_range_before=2025-12-15T23:59:59Z
        """