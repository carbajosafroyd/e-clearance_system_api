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

from api.locker_system.models import LockerSubscriptionRequest
from api.locker_system.serializers import (
    LockerSubscriptionRequestCreateSerializer,
    LockerSubscriptionRequestReadSerializer,
    LockerSubscriptionRequestStatusSerializer,
    LockerSubscriptionRequestReadSlimSerializer,
)
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination
from api.fees.models import Fee
from api.collection_categories.models import CollectionCategory


@extend_schema(tags=["Locker Subscription Requests"])
class LockerSubscriptionRequestViewSet(viewsets.ModelViewSet):
    # The read serializers nest the student (program, and user on retrieve)
    # and the locker (building/institute, each rendering their school).
    queryset = LockerSubscriptionRequest.objects.select_related(
        "student__program",
        "student__user",
        "locker__building__school",
        "locker__institute__school",
    ).order_by("-updated_at")
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "locker_system",
        "lockersubscriptionrequest",
        approve="locker_system.approve_lockersubscriptionrequest",
        reject="locker_system.approve_lockersubscriptionrequest",
    )
    pagination_class = CustomPagination
    ordering_fields = ["id"]

    def get_serializer_class(self):
        if self.action == "create":
            return LockerSubscriptionRequestCreateSerializer
        if self.action == "retrieve":
            return LockerSubscriptionRequestReadSerializer
        if self.action in ["update", "partial_update"]:
            return LockerSubscriptionRequestCreateSerializer
        elif self.action in ["approve", "reject"]:
            return LockerSubscriptionRequestStatusSerializer
        return LockerSubscriptionRequestReadSlimSerializer

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, "student"):
            return self.queryset.filter(student=user.student)
        return self.queryset

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        with transaction.atomic():
            subscription_request = (
                LockerSubscriptionRequest.objects.select_for_update().get(pk=pk)
            )

            if (
                subscription_request.status
                != LockerSubscriptionRequest.SubscriptionStatus.PENDING
            ):
                return Response(
                    {"detail": "Only pending requests can be approved."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            subscription_request.status = (
                LockerSubscriptionRequest.SubscriptionStatus.APPROVED
            )
            subscription_request.save(update_fields=["status"])

            payment_due_at = timezone.now() + timedelta(days=31)

            LockerSubscription.objects.create(
                student=subscription_request.student,
                locker=subscription_request.locker,
                academic_year=subscription_request.academic_year,
                semester=subscription_request.semester,
                subscription_request=subscription_request,
                payment_due_at=payment_due_at,
                created_by=request.user,
                status=LockerSubscription.SubscriptionStatus.ACTIVE,
                payment_status=LockerSubscription.SubscriptionPaymentStatus.UNPAID,
            )

            category = CollectionCategory.objects.get(
                category_name="Locker",
                institute=subscription_request.student.program.institute,
            )

            amount = category.collection_fee

            Fee.objects.create(
                student=subscription_request.student,
                category=category,
                total_amount=amount,
                balance=amount,
                status=Fee.FeeStatus.PENDING,
                academic_year=subscription_request.academic_year,
                semester=subscription_request.semester,
                issued_by=request.user,
            )

            return Response(
                LockerSubscriptionRequestReadSerializer(subscription_request).data,
                status=status.HTTP_200_OK,
            )
