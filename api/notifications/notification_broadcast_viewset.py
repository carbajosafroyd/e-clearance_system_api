import logging

from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from api.notifications.models import Notification, NotificationBroadcast
from api.notifications.serializers import NotificationBroadcastSerializer
from api.notifications.serializers.broadcast_read_serializer import (
    NotificationBroadcastReadSerializer,
)
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination

logger = logging.getLogger(__name__)


@extend_schema(tags=["Notifications Broadcast"])
class NotificationBroadcastViewSet(viewsets.GenericViewSet):
    """
    ViewSet for administrative broadcast management.
    Supports global alerts (Superadmin) and scoped multi-tenant targeting (Staff).
    """

    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "notifications",
        "notificationbroadcast",
    )
    pagination_class = CustomPagination

    def get_queryset(self):  # type: ignore
        return NotificationBroadcast.objects.select_related(
            "sender", "target_institute", "target_program"
        )

    def get_serializer_class(self):  # type: ignore
        if self.action == "create":
            return NotificationBroadcastSerializer
        return NotificationBroadcastReadSerializer

    def create(self, request, *args, **kwargs):
        """
        System broadcast mechanism supporting both global alerts (Superadmin)
        and scoped multi-tenant criteria targeting (Staff).
        """
        serializer = self.get_serializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        broadcast = serializer.save()

        return Response(
            {
                "message": "Notification broadcast successfully sent.",
                "broadcast_id": broadcast.id,
                "total_recipients": broadcast.total_recipients,
                "created_at": broadcast.created_at,
            },
            status=status.HTTP_201_CREATED,
        )

    def list(self, request, *args, **kwargs):
        """
        Retrieves historical logs of administrative announcements.
        Multi-tenant scoped and safely paginated.
        """
        queryset = self.get_queryset()

        if not request.user.is_superuser:
            queryset = queryset.filter(target_institute=request.user.institute)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def retrieve(self, request, pk=None, *args, **kwargs):
        """Retrieves details of a specific broadcast announcement by ID."""
        broadcast = get_object_or_404(NotificationBroadcast, pk=pk)

        if (
            not request.user.is_superuser
            and broadcast.target_institute != request.user.institute
        ):
            return Response(
                {
                    "detail": "You do not have permission to view this broadcast resource."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = self.get_serializer(broadcast)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def destroy(self, request, pk=None, *args, **kwargs):
        """
        Deletes a historic broadcast record tracker log.
        Note: Individual student notifications persist because on_delete=SET_NULL.
        """
        broadcast = get_object_or_404(NotificationBroadcast, pk=pk)

        if (
            not request.user.is_superuser
            and broadcast.target_institute != request.user.institute
        ):
            return Response(
                {
                    "detail": "You do not have permission to delete this broadcast record."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        broadcast.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
