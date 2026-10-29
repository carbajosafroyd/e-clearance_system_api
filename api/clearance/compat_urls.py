from django.urls import re_path

from api.clearance.compat_views import (
    ClearanceBridgeHealthView,
    CollectionCategoriesView,
    CollectionQueueDetailView,
    CollectionQueuesView,
    CurrentCollectionCategoriesView,
    IdentityResolveView,
    StudentCollectionStatusView,
    StudentListView,
)

PREFIX = r"^clearance/bridge/"

urlpatterns = [
    re_path(PREFIX + r"identity/resolve/?$", IdentityResolveView.as_view(), name="clms-identity-resolve"),
    re_path(PREFIX + r"students/?$", StudentListView.as_view(), name="clms-students"),
    re_path(
        PREFIX + r"students/(?P<student_number>[^/]+)/collection-status/?$",
        StudentCollectionStatusView.as_view(),
        name="clms-student-collection-status",
    ),
    re_path(PREFIX + r"collection/categories/?$", CollectionCategoriesView.as_view(), name="clms-collection-categories"),
    re_path(
        PREFIX + r"collection/categories/current/?$",
        CurrentCollectionCategoriesView.as_view(),
        name="clms-current-collection-categories",
    ),
    re_path(PREFIX + r"collection/queues/?$", CollectionQueuesView.as_view(), name="clms-collection-queues"),
    re_path(
        PREFIX + r"collection/queues/(?P<queue_id>[^/]+)/?$",
        CollectionQueueDetailView.as_view(),
        name="clms-collection-queue-detail",
    ),
    re_path(PREFIX + r"health/?$", ClearanceBridgeHealthView.as_view(), name="clms-clearance-bridge-health"),
]
