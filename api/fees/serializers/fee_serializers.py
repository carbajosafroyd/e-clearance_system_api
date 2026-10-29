from decimal import Decimal
from typing import Any

from rest_framework import serializers

from api.attendance_records.models import AttendanceRecord
from api.collection_categories import serializers as CollectionCategorySerializer
from api.students import serializers as StudentSerializers

from ..models import Fee


class NetExpectedMixin(serializers.Serializer):
    """Adds `net_expected` — what is actually owed once relief is taken off.

    Derived rather than stored, so it can never disagree with the two columns
    it comes from. Read serializers carry `total_amount` (gross) and
    `discount_total` (relief) as well, because a client showing only the net
    figure cannot explain why it differs from the amount on the receipt.
    """

    net_expected = serializers.SerializerMethodField()

    def get_net_expected(self, obj) -> Decimal:
        return obj.total_amount - obj.discount_total


class FeeReadSerializer(serializers.ModelSerializer):
    student = StudentSerializers.StudentReadMiniSerializer()
    category = CollectionCategorySerializer.CollectionCategoryMiniSerializer()
    issued_by = serializers.StringRelatedField()
    payments = serializers.SerializerMethodField()

    class Meta:
        model = Fee
        fields = "__all__"

    def get_payments(self, obj) -> Any:
        # Imported here rather than at module scope: payments.serializers
        # imports this module back, so a top-level import deadlocks on load.
        from api.payments.serializers import FeePaymentsReadMiniSerializer

        return FeePaymentsReadMiniSerializer(
            obj.payments.all(), many=True, context=self.context
        ).data


class FeeReadSlimSerializer(NetExpectedMixin, serializers.ModelSerializer):
    student = StudentSerializers.StudentReadMiniSerializer()
    category_id = serializers.CharField(source="category.id", read_only=True)
    category_name = serializers.CharField(
        source="category.category_name", read_only=True
    )
    # issued_by is SET_NULL: once the staff account that raised the fee is
    # deleted, traversing `.username` on None would blow up mid-serialisation.
    issued_by = serializers.CharField(
        source="issued_by.username", read_only=True, allow_null=True
    )
    batch_id = serializers.IntegerField(source="gen_fee_batch_id", read_only=True)

    class Meta:
        model = Fee
        fields = [
            "id",
            "student",
            "category_id",
            "category_name",
            "total_amount",
            "discount_total",
            "net_expected",
            "balance",
            "status",
            "due_date",
            "issued_by",
            "remarks",
            "academic_year",
            "semester",
            "batch_id",
        ]
        read_only_fields = fields


class FeeReadMiniSerializer(NetExpectedMixin, serializers.ModelSerializer):
    student = StudentSerializers.StudentReadMiniSerializer()
    category_id = serializers.CharField(source="category.id", read_only=True)
    category_name = serializers.CharField(
        source="category.category_name", read_only=True
    )

    class Meta:
        model = Fee
        fields = [
            "id",
            "student",
            "category_id",
            "category_name",
            "total_amount",
            "discount_total",
            "net_expected",
            "balance",
            "status",
            "due_date",
        ]
        read_only_fields = fields


class FeeReadMiniV2Serializer(NetExpectedMixin, serializers.ModelSerializer):
    """Same as FeeReadMiniSerializer without the nested student block, for
    payloads that already carry the student alongside."""

    category_id = serializers.CharField(source="category.id", read_only=True)
    category_name = serializers.CharField(
        source="category.category_name", read_only=True
    )

    class Meta:
        model = Fee
        fields = [
            "id",
            "category_id",
            "category_name",
            "total_amount",
            "discount_total",
            "net_expected",
            "balance",
            "status",
            "due_date",
        ]
        read_only_fields = fields


class FeeCreateSerializer(serializers.ModelSerializer):
    # No `status`: a fee is born PENDING. Waiving is a dedicated action with
    # required remarks, PAID is set by the payment service at zero balance,
    # and OVERDUE comes only from the automation.
    class Meta:
        model = Fee
        fields = [
            "student",
            "category",
            "total_amount",
            "due_date",
            "remarks",
            "academic_year",
            "semester",
        ]

    def validate_total_amount(self, value):
        if value < 0:
            raise serializers.ValidationError("Total amount cannot be negative.")
        return value

    def create(self, validated_data):
        # A fee starts out fully unpaid. Deriving this here rather than in the
        # viewset keeps the invariant true for every caller, including tests
        # and management commands that use the serializer directly.
        validated_data.setdefault("balance", validated_data["total_amount"])
        return super().create(validated_data)


class FeeUpdateSerializer(serializers.ModelSerializer):
    """Deliberately narrow. `balance` moves only through the payment service,
    `status` only through waive/unwaive, payments and the overdue automation,
    and `total_amount` is fixed at issue - an amount correction is a
    cancellation plus a new fee, not an edit."""

    class Meta:
        model = Fee
        fields = [
            "due_date",
            "remarks",
        ]


class FeeWaiveSerializer(serializers.Serializer):
    """Input for POST /fees/{id}/waive/."""

    remarks = serializers.CharField(required=True, allow_blank=False)


class FeeDeleteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Fee
        fields = ["id"]


class AttendanceRecordReadMiniSerializer(serializers.ModelSerializer):
    updated_by = serializers.StringRelatedField()

    class Meta:
        model = AttendanceRecord
        exclude = [
            "student",
            "fee",
            "institute_attendance_event",
            "created_at",
            "updated_at",
        ]


class AttendanceFeeBreakdownSerializer(serializers.Serializer):
    """Response shape for /fees/{id}/attendance-details/."""

    event_name = serializers.CharField(allow_null=True)
    attendance_records = AttendanceRecordReadMiniSerializer(many=True)
    grand_fee_total = serializers.DecimalField(max_digits=12, decimal_places=2)
    has_record = serializers.BooleanField()
    missed_days = serializers.ListField(child=serializers.DateField())
    message = serializers.CharField()


class AttendanceFeeSettingsSerializer(serializers.Serializer):
    """Response shape for /fees/{id}/attendance-settings/."""

    event = serializers.DictField()
    daily_settings = serializers.ListField(child=serializers.DictField())
