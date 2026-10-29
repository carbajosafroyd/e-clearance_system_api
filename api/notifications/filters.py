import django_filters
from django.db.models import Q
from api.notifications.models import Notification


class NotificationFilter(django_filters.FilterSet):
    # Custom text search across title and message
    search = django_filters.CharFilter(method="filter_search")

    # created_at is a datetime; the `date` lookup keeps `created_to` from
    # dropping everything that arrived after midnight on that day.
    created_from = django_filters.DateFilter(field_name="created_at", lookup_expr="date__gte")
    created_to = django_filters.DateFilter(field_name="created_at", lookup_expr="date__lte")
    #: Comma-separated, e.g. `?type__in=fees.pending,payment.approved`.
    type__in = django_filters.BaseInFilter(field_name="type", lookup_expr="in")
    source__in = django_filters.BaseInFilter(field_name="source", lookup_expr="in")

    class Meta:
        model = Notification
        fields = {
            "type": ["exact"],
            "source": ["exact"],
            "is_read": ["exact"],
            "broadcast": ["exact"],
        }

    def filter_search(self, queryset, name, value):
        """
        Allows the frontend to pass `?search=xyz` to fuzzy-match
        both the notification title and the body content.
        """
        if not value:
            return queryset

        return queryset.filter(Q(title__icontains=value) | Q(message__icontains=value))
