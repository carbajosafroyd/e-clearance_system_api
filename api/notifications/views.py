from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.notifications.models import Notification
from api.notifications.filters import NotificationFilter
from api.notifications.serializers import (
    NotificationListSerializer,
    NotificationDetailSerializer,
)
from api.notifications import service as notification_service
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination


@extend_schema(tags=["Notifications"])
class NotificationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """
    ViewSet for handling user notifications.
    Provides high-performance filtering, safe side-effect read marks.
    """

    permission_classes = [IsAuthenticated, ActionPermission]
    # Every action here is scoped to the caller's own rows by get_queryset(),
    # so `view_notification` is the whole gate; marking one read is a change
    # to your own record, not to somebody else's.
    required_permissions = crud(
        "notifications",
        "notification",
        unread_count="notifications.view_notification",
        mark_all_read="notifications.change_notification",
    )
    filter_backends = [DjangoFilterBackend]
    filterset_class = NotificationFilter
    pagination_class = CustomPagination
    # The schema generator needs to know the model without a request in
    # hand; get_serializer_class() picks the real one per action.
    queryset = Notification.objects.none()
    serializer_class = NotificationListSerializer

    def get_queryset(self): # type: ignore
        # Schema generation calls this with an AnonymousUser, which the
        # `user=` filter cannot resolve to an id.
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()

        return (
            Notification.objects.filter(user=self.request.user)
            .select_related("sender")
            .order_by("is_read", "-created_at")
        )

    def get_serializer_class(self): # type: ignore
        if self.action == "list":
            return NotificationListSerializer
        if self.action == "retrieve":
            return NotificationDetailSerializer
        return super().get_serializer_class()

    def retrieve(self, request, *args, **kwargs):
        """Retrieves a single notification and flips its read status as an implicit side-effect."""
        instance = self.get_object()

        if not instance.is_read:
            instance = notification_service.mark_as_read(instance)

        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @extend_schema(
        responses=inline_serializer(
            "NotificationUnreadCount",
            {"unread_count": serializers.IntegerField()},
        )
    )
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        """Fetches a lightweight, cache-friendly count of all current unread notifications."""
        count = notification_service.get_unread_count(request.user)
        return Response({"unread_count": count}, status=status.HTTP_200_OK)

    @extend_schema(
        request=None,
        responses=inline_serializer(
            "NotificationMarkAllRead",
            {"updated_count": serializers.IntegerField()},
        ),
    )
    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        """Bulk updates all remaining unread notification records for the active requester."""
        updated_count = notification_service.mark_all_as_read(request.user)
        return Response(
            {"updated_count": updated_count},
            status=status.HTTP_200_OK,
        )
