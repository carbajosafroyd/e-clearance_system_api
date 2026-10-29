
from rest_framework import viewsets
from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import snapshot_instance
from api.payments.models import Payment
from api.rbac import ActionPermission, crud
from api.utils.custom_paginations import CustomPagination
from rest_framework.permissions import (
    IsAuthenticated,
    IsAdminUser,
    DjangoModelPermissions,
)
from .serializers import (
    PaymentCancelSerializer,
    PaymentCreateSerializer,
    PaymentReadSerializer,
    PaymentReadSlimSerializer,
    PaymentDeleteSerializer,
)

from .filters import PaymentFilterSet
from .notifications import notify_payment_cancelled, notify_payments_received

from drf_spectacular.utils import extend_schema

from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from .serializers import PaymentBulkCreateSerializer
from . import services as payment_services


@extend_schema(tags=["Payments"])
class PaymentViewSet(viewsets.ModelViewSet):
    # fee__student__program: FeeReadMini/Slim nest StudentReadMini, which reads
    # program.name. fee__issued_by: FeeReadSlim (retrieve) reads its username.
    # payment_submission__student: the StringRelatedField renders
    # PaymentSubmission.__str__, which reads student.s_studentID.
    queryset = Payment.objects.select_related(
        'fee__student__program',
        'fee__category',
        'fee__issued_by',
        'received_by',
        'cancelled_by',
        'payment_submission__student',
    ).all().order_by('-created_at')
    pagination_class = CustomPagination  # list actions use this
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "payments",
        "payment",
        bulk_create="payments.bulk_add_payment",
        cancel="payments.cancel_payment",
    )

    # No PUT/PATCH: payments are immutable ledger rows. An encoding mistake
    # is undone with POST /payments/{id}/cancel/, never edited in place.
    http_method_names = ["get", "post", "delete", "head", "options"]

    filterset_class = PaymentFilterSet
    search_fields = ["fee__student__s_fname", "fee__student__s_lname"]
    ordering_fields = ["-updated_at"]

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        payment = serializer.save()
        notify_payments_received(payments=[payment], actor=self.request.user)

    def destroy(self, request, *args, **kwargs):
        # Hard deletes lose the money trail (and never restored the balance).
        # Kept superuser-only for genuine data cleanup; everyone else cancels.
        if not request.user.is_superuser:
            return Response(
                {
                    "detail": (
                        "Deleting payments is disabled - cancel the payment "
                        "instead so the fee balance is restored."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        entity_id = instance.pk
        snapshot = snapshot_instance(instance)
        instance.delete()
        AuditLogService.log_model_delete(
            entity_type="Payment",
            entity_id=entity_id,
            snapshot=snapshot,
            user=self.request.user,
            system=AuditLog.System.COLLECTION,
        )

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser:
            return self.queryset
        if user.is_staff:
            return self.queryset.filter(fee__student__program__institute=user.institute)
        if hasattr(user, "student"):
            return self.queryset.filter(fee__student=user.student)
        return self.queryset.none()

    def get_serializer_class(self):
        if self.action == "create":
            return PaymentCreateSerializer
        elif self.action == "retrieve":
            return PaymentReadSerializer
        elif self.action == "destroy":
            return PaymentDeleteSerializer
        elif self.action == "bulk_create":
            return PaymentBulkCreateSerializer
        elif self.action == "cancel":
            return PaymentCancelSerializer
        return PaymentReadSlimSerializer

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk_create(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payments = serializer.save()

        notify_payments_received(payments=payments, actor=request.user)

        return Response(
            {
                "message": "Bulk payment successful",
                "count": len(payments),
            },
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=PaymentCancelSerializer,
        responses=PaymentReadSerializer,
        description=(
            "Cancel an over-the-counter payment and restore the fee's "
            "balance. Payments born from a submission must be cancelled "
            "through the submission's cancel action instead."
        ),
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        payment = self.get_object()
        serializer = PaymentCancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_snapshot = snapshot_instance(payment)
        cancelled = payment_services.cancel_payment(
            payment=payment,
            actor=request.user,
            reason=serializer.validated_data["reason"],
        )

        AuditLogService.log_model_update(
            instance=cancelled,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
            action=AuditLog.Action.CANCEL,
        )

        notify_payment_cancelled(payment=cancelled, actor=request.user)

        return Response(
            PaymentReadSerializer(cancelled, context={"request": request}).data
        )
