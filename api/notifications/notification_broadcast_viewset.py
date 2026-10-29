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
from api.notifications.serializers.broadcast_serializer import (
    OverdueBroadcastResponseSerializer,
)
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination

from .service import broadcast_pending_fees

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
        broadcast_pending_fees="notifications.send_notificationbroadcast",
        notify_overdue_fees="notifications.send_notificationbroadcast",
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

    @extend_schema(request=None)
    @action(detail=False, methods=["post"], url_path="pending-fees")
    def broadcast_pending_fees(self, request):
        broadcast = broadcast_pending_fees(sender=request.user)
        return Response(
            {
                "detail": "Broadcast successfully processed.",
                "total_recipients": broadcast.total_recipients,
            },
            status=status.HTTP_201_CREATED,
        )
    
    @extend_schema(
        request=None,
        responses=OverdueBroadcastResponseSerializer,
        description=(
            "Bring overdue fees up to date and tell the students in one call: "
            "past-due PENDING fees in your institute are flipped to OVERDUE, "
            "then every student who currently holds an OVERDUE fee gets one "
            "notification listing their own fees, and you get a receipt."
        ),
    )
    @action(detail=False, methods=["post"], url_path="notify-overdue-fees")
    def notify_overdue_fees(self, request):
        """
        Update, then notify - deliberately one action.

        These were split once, and the split was the problem: the sweep ran on
        a timer and told nobody, so the statuses were right and the students
        were not. Doing both here means the notification exists because an
        officer asked for it, which was the real objection to the old
        behaviour - not the telling, but the telling on every automatic sweep.

        The silent sweep is still available on its own at
        `POST /fees/update-overdue-fees/` for schedulers, which is where an
        untriggered status update belongs.

        Note the two counts mean different things. `updated_count` is what
        this call changed; `total_recipients` is everybody carrying an overdue
        fee right now, including students who were already overdue yesterday -
        a reminder is for them too.
        """
        # Imported here, not at module scope: api.fees imports this package
        # for its generation notifications, so a top-level import closes the
        # loop and breaks startup.
        from api.fees.services.overdue_service import mark_fees_as_overdue

        from .service import broadcast_overdue_fees

        sender = request.user
        result = mark_fees_as_overdue(actor=sender)

        try:
            broadcast = broadcast_overdue_fees(sender)
        except ValidationError:
            # Nothing is overdue at all - so nothing was flipped either, and
            # there is nobody to tell. That is a finished job, not a failure.
            logger.info(
                "notification.broadcast.overdue_fees nothing_overdue sender=%s",
                sender.username,
            )
            serializer = OverdueBroadcastResponseSerializer(
                {
                    "updated_count": result.updated_count,
                    "broadcast_id": 0,
                    "title": "Overdue Fee Notice",
                    "message": "No fee is overdue. Nobody was notified.",
                    "total_recipients": 0,
                    "notified_user_ids": [],
                }
            )
            return Response(serializer.data, status=status.HTTP_200_OK)

        notified_user_ids = list(
            Notification.objects.filter(broadcast=broadcast).values_list(
                "user_id", flat=True
            )
        )

        # No sender receipt here: `broadcast_overdue_fees` already sends one
        # ("Overdue Fee Broadcast Complete", listing who was reached). Adding
        # a second meant the operator got told twice for one click.

        logger.info(
            "notification.broadcast.overdue_fees sender=%s updated=%s recipients=%s",
            sender.username,
            result.updated_count,
            broadcast.total_recipients,
        )

        serializer = OverdueBroadcastResponseSerializer(
            {
                "updated_count": result.updated_count,
                "broadcast_id": broadcast.id,
                "title": broadcast.title,
                "message": broadcast.message,
                "total_recipients": broadcast.total_recipients,
                "notified_user_ids": notified_user_ids,
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)
