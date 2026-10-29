import logging

from django.db.models import Case, IntegerField, Prefetch, Value, When
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import snapshot_instance
from api.contributions import selectors as contribution_selectors
from api.contributions.serializers import FeeDiscountReadSerializer
from api.notifications.serializers.broadcast_serializer import (
    MarkFeesOverdueResponseSerializer,
)
from api.rbac import ActionPermission, Feature, crud
from api.utils import custom_paginations
from core import features

from .. import cache as fee_cache
from .. import selectors
from ..constants import ATTENDANCE_CATEGORY_NAME
from ..filters import FeesFilterSet
from ..models import Fee
from ..permissions import IsWithinInstituteScope
from ..serializers import (
    AttendanceFeeBreakdownSerializer,
    AttendanceFeeSettingsSerializer,
    FeeCreateSerializer,
    FeeDeleteSerializer,
    FeeReadSerializer,
    FeeReadSlimSerializer,
    FeeUpdateSerializer,
    FeeWaiveSerializer,
)
from ..services import mark_fees_as_overdue, unwaive_fee, waive_fee

logger = logging.getLogger(__name__)


@extend_schema(tags=["Fee"])
class FeeViewSet(viewsets.ModelViewSet):
    queryset = Fee.objects.select_related(
        "student",
        "student__program",
        "category",
        "issued_by",
    ).order_by(
        # Unpaid first, then most recently touched.
        Case(
            When(status=Fee.FeeStatus.OVERDUE, then=Value(0)),
            When(status=Fee.FeeStatus.PENDING, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        ),
        "-updated_at",
    )

    pagination_class = custom_paginations.CustomPagination
    permission_classes = [
        IsAuthenticated,
        ActionPermission,
        IsWithinInstituteScope,
    ]
    # Waiving a debt and editing a fee are different authorities; keeping
    # them apart is the whole reason this table exists. Add an @action, add
    # a line here - `ActionPermission` denies anything it does not find.
    required_permissions = crud(
        "fees",
        "fee",
        waive="fees.waive_fee",
        unwaive="fees.unwaive_fee",
        update_overdue_fees="fees.mark_fee_overdue",
        attendance_details="fees.view_fee",
        attendance_settings="fees.view_fee",
        # Optional module: with the contribution scheme off there is no
        # such thing as a points discount, so the route refuses rather than
        # returning an empty list that implies the feature exists.
        discounts=Feature(
            features.CONTRIBUTION_POINTS, "fees.view_fee_discount_history"
        ),
        # Both kinds of relief in one list. Not feature-gated: it is the
        # answer to "why is this fee smaller than it was billed", and that
        # question outlives whichever scheme was switched on at the time.
        reductions="fees.view_fee",
    )

    filterset_class = FeesFilterSet
    search_fields = [
        "student__s_fname",
        "student__s_mname",
        "student__s_lname",
        # Suffix included for the same reason as mname: the UI shows the full
        # name and SearchFilter ANDs every typed word, so "Juan Cruz Jr."
        # must not come back empty.
        "student__s_suffix",
        "student__s_studentID",
        "category__category_name",
    ]
    ordering_fields = [
        "created_at",
        "updated_at",
        "due_date",
        "total_amount",
        "discount_total",
        "balance",
    ]

    def get_serializer_class(self):  # type: ignore
        if self.action == "create":
            return FeeCreateSerializer
        if self.action == "retrieve":
            return FeeReadSerializer
        if self.action in ("update", "partial_update"):
            return FeeUpdateSerializer
        if self.action == "destroy":
            return FeeDeleteSerializer
        return FeeReadSlimSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = super().get_queryset()

        if self.action == "retrieve":
            # FeeReadSerializer embeds the fee's payments, and each one renders
            # received_by.username - prefetch both here or that's one query per
            # payment. Imported locally for the same circular-import reason as
            # in fee_serializers.py.
            from api.payments.models import Payment

            queryset = queryset.prefetch_related(
                Prefetch(
                    "payments",
                    queryset=Payment.objects.select_related(
                        "received_by", "cancelled_by"
                    ),
                )
            )

        if user.is_superuser:
            return queryset

        if user.is_staff:
            institute_id = getattr(user, "institute_id", None)
            if not institute_id:
                return queryset.none()
            return queryset.filter(student__program__institute_id=institute_id)

        student = getattr(user, "student", None)
        if student is not None:
            return queryset.filter(student=student)

        return queryset.none()

    def list(self, request, *args, **kwargs):
        """
        Cached per caller and per query string; see `fees/cache.py`.

        The whole paginated payload is cached, not the queryset, so a hit
        skips the count query, the page query and the serialization. Any fee
        or batch write bumps the namespace and empties this out.
        """
        key = fee_cache.build_key(
            fee_cache.LIST_NS, fee_cache.request_parts(request, "fee-list")
        )

        data = fee_cache.read(key)
        if data is None:
            data = super().list(request, *args, **kwargs).data
            fee_cache.write(key, data)
        return Response(data)

    def perform_create(self, serializer):
        # `balance` is derived from `total_amount` inside FeeCreateSerializer.
        serializer.save(issued_by=self.request.user)

    def perform_update(self, serializer):
        old_snapshot = snapshot_instance(serializer.instance)
        fee = serializer.save()
        AuditLogService.log_model_update(
            instance=fee,
            old_snapshot=old_snapshot,
            user=self.request.user,
            system=AuditLog.System.COLLECTION,
        )

    def perform_destroy(self, instance):
        entity_id = instance.pk
        snapshot = snapshot_instance(instance)
        instance.delete()
        AuditLogService.log_model_delete(
            entity_type="Fee",
            entity_id=entity_id,
            snapshot=snapshot,
            user=self.request.user,
            system=AuditLog.System.COLLECTION,
        )

    def _get_attendance_fee(self) -> Fee:
        fee = self.get_object()
        if not selectors.is_attendance_fee(fee):
            raise ValidationError(
                {"detail": f"Fee category must be '{ATTENDANCE_CATEGORY_NAME}'."}
            )
        return fee

    @extend_schema(responses=AttendanceFeeBreakdownSerializer)
    @action(detail=True, methods=["get"], url_path="attendance-details")
    def attendance_details(self, request, pk=None):
        """Per-day attendance behind this fee, including the days with no record."""
        # `_get_attendance_fee()` runs get_object(), and with it the institute
        # scope check - so the cache is only ever reached by someone already
        # allowed to see this fee. The key is the fee id alone because the
        # payload is the same whoever asks.
        fee = self._get_attendance_fee()
        return Response(
            fee_cache.get_or_set(
                fee_cache.ATTENDANCE_NS,
                ["attendance-details", fee.pk],
                lambda: selectors.attendance_breakdown(fee),
            )
        )

    @extend_schema(responses=AttendanceFeeSettingsSerializer)
    @action(detail=True, methods=["get"], url_path="attendance-settings")
    def attendance_settings(self, request, pk=None):
        """The event configuration this fee was calculated against."""
        fee = self._get_attendance_fee()
        # A fee with no event linked yields None, which the cache treats as a
        # miss and so never stores. That is fine: it is the cheap path anyway.
        snapshot = fee_cache.get_or_set(
            fee_cache.ATTENDANCE_NS,
            ["attendance-settings", fee.pk],
            lambda: selectors.attendance_settings_snapshot(fee),
        )
        if snapshot is None:
            return Response(
                {"detail": "No attendance event is linked to this fee."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(snapshot)

    @extend_schema(
        request=FeeWaiveSerializer,
        responses=FeeReadSlimSerializer,
        description=(
            "Forgive the remaining balance of a pending/overdue fee. Remarks "
            "are required; money already received stays received - undoing "
            "that is a payment/submission cancellation."
        ),
    )
    @action(detail=True, methods=["post"])
    def waive(self, request, pk=None):
        fee = self.get_object()
        serializer = FeeWaiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_snapshot = snapshot_instance(fee)
        fee = waive_fee(
            fee=fee,
            actor=request.user,
            remarks=serializer.validated_data["remarks"],
        )
        AuditLogService.log_model_update(
            instance=fee,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
        )
        return Response(
            FeeReadSlimSerializer(fee, context={"request": request}).data
        )

    @extend_schema(
        request=None,
        responses=FeeReadSlimSerializer,
        description="Put a waived fee back to pending; its balance is owed again.",
    )
    @action(detail=True, methods=["post"])
    def unwaive(self, request, pk=None):
        fee = self.get_object()

        old_snapshot = snapshot_instance(fee)
        fee = unwaive_fee(fee=fee, actor=request.user)
        AuditLogService.log_model_update(
            instance=fee,
            old_snapshot=old_snapshot,
            user=request.user,
            system=AuditLog.System.COLLECTION,
        )
        return Response(
            FeeReadSlimSerializer(fee, context={"request": request}).data
        )

    @extend_schema(
        responses=FeeDiscountReadSerializer(many=True),
        description=(
            "Every points-based discount ever requested against this fee, "
            "newest first - rejected and cancelled ones included, because "
            "'we asked and were refused' is part of the history. An approved "
            "discount raised the fee's discount_total and lowered its balance, "
            "and each "
            "row carries the pair of figures from before it was applied."
        ),
    )
    @action(detail=True, methods=["get"])
    def discounts(self, request, pk=None):
        # get_object() runs the institute-scope check, so a caller reaching
        # this has already been allowed to see the fee itself.
        fee = self.get_object()
        return Response(
            FeeDiscountReadSerializer(
                contribution_selectors.fee_discount_history(fee=fee),
                many=True,
                context={"request": request},
            ).data
        )

    @extend_schema(
        summary="Everything that has been taken off this fee",
        description=(
            "Points discounts and coupon redemptions in one list, newest "
            "first. A fee's `discount_total` is the sum of these; without "
            "them a total that shrank has no explanation anywhere. "
            "Not gated on either scheme's flag: relief granted last semester "
            "still needs explaining after the scheme that granted it is "
            "switched off."
        ),
        responses={200: OpenApiTypes.OBJECT},
    )
    @action(detail=True, methods=["get"])
    def reductions(self, request, pk=None):
        # get_object() has already applied the institute/ownership scope.
        fee = self.get_object()
        rows = []

        if features.is_enabled(features.CONTRIBUTION_POINTS):
            for discount in contribution_selectors.fee_discount_history(fee=fee):
                rows.append(
                    {
                        "kind": "points",
                        "id": discount.pk,
                        "label": f"{discount.points_spent} points",
                        "amount": discount.amount,
                        "status": discount.status,
                        "reason": discount.remarks or None,
                        "at": discount.reviewed_at or discount.created_at,
                    }
                )

        if features.is_enabled(features.COUPONS):
            from api.coupons.models import CouponRedemption

            redemptions = (
                CouponRedemption.objects.filter(fee=fee)
                .select_related("coupon__batch")
                .order_by("-created_at")
            )
            for redemption in redemptions:
                rows.append(
                    {
                        "kind": "coupon",
                        "id": redemption.pk,
                        "label": redemption.coupon.code,
                        "amount": redemption.amount,
                        "status": redemption.status,
                        "reason": (
                            redemption.coupon.batch.reason
                            or redemption.coupon.batch.name
                        ),
                        "at": redemption.applied_at or redemption.created_at,
                    }
                )

        rows.sort(key=lambda row: row["at"], reverse=True)
        return Response(
            {
                "total_amount": fee.total_amount,
                "discount_total": fee.discount_total,
                "net_expected": fee.total_amount - fee.discount_total,
                "balance": fee.balance,
                "reductions": rows,
            }
        )

    @extend_schema(request=None, responses=MarkFeesOverdueResponseSerializer)
    @action(detail=False, methods=["post"], url_path="update-overdue-fees")
    def update_overdue_fees(self, request):
        """
        Flip past-due PENDING fees in your institute to OVERDUE.

        Statuses only: no student is notified and no summary is sent back to
        the caller. This is the scheduler's endpoint.

        To update *and* tell the students in one go - the dashboard's
        "Notify Overdue" - use
        `POST /notifications-broadcast/notify-overdue-fees/`.
        """
        result = mark_fees_as_overdue(actor=request.user)
        return Response(
            MarkFeesOverdueResponseSerializer(result).data,
            status=status.HTTP_200_OK,
        )
