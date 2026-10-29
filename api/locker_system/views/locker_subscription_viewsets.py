from django.db import transaction
from django.shortcuts import get_object_or_404

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from drf_spectacular.utils import extend_schema

from django.utils import timezone
from datetime import timedelta

from api.locker_system.models import LockerSubscription

from api.locker_system.serializers import (
    LockerSubscriptionCancelSerializer,
    LockerSubscriptionCreateSerializer,
    LockerSubscriptionReadSerializer,
    LockerSubscriptionSlimSerializer,
)
from api.utils.MixIns import UserContextMixin
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination


@extend_schema(tags=["Locker Subscriptions"])
class LockerSubscriptionViewSet(viewsets.ModelViewSet):
    # The read serializers walk a lot of edges per row: student.program, the
    # locker (building/institute with their school), the FeeReadMini block
    # (fee.student.program + fee.category), created_by/cancelled_by, and the
    # subscription_request StringRelatedField - whose __str__ itself reads
    # the request's student and locker.
    queryset = LockerSubscription.objects.select_related(
        "student__program",
        "locker__building__school",
        "locker__institute__school",
        "subscription_request__student",
        "subscription_request__locker",
        "created_by",
        "cancelled_by",
        "fee__student__program",
        "fee__category",
    ).order_by("-subscribed_at")
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "locker_system",
        "lockersubscription",
        cancel="locker_system.cancel_lockersubscription",
    )
    pagination_class = CustomPagination
    ordering_fields = ["id"]

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return LockerSubscriptionCreateSerializer
        elif self.action == "cancel":
            return LockerSubscriptionCancelSerializer
        elif self.action == "retrieve":
            return LockerSubscriptionReadSerializer
        else:
            return LockerSubscriptionSlimSerializer

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        if user.is_staff:
            return self.queryset.filter(student__program__institute=user.institute)
        elif hasattr(user, "student"):
            return self.queryset.filter(student=user.student)
        else:
            return self.queryset.none()

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        subscription = self.get_object()

        if subscription.status == LockerSubscription.SubscriptionStatus.CANCELLED:
            return Response(
                {"detail": "Subscription is already cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscription.status = LockerSubscription.SubscriptionStatus.CANCELLED
        subscription.save()

        return Response({"detail": "Subscription cancelled successfully."})
