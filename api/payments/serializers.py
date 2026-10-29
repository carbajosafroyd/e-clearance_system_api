from rest_framework import serializers
from api.fees.models import Fee
from api.payments.models import Payment
from api.fees.serializers import FeeReadMiniSerializer, FeeReadSlimSerializer
from django.db import transaction

from . import services as payment_services


class PaymentCreateSerializer(serializers.ModelSerializer):
    """Over-the-counter payments. Submission-born payments are created by the
    approve action through the same service - never through this endpoint -
    which is why `payment_submission` is not accepted here."""

    class Meta:
        model = Payment
        fields = ["fee", "amount_paid", "payment_method"]

    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get("request")
        return payment_services.record_payment(
            fee=validated_data["fee"],
            amount=validated_data["amount_paid"],
            method=validated_data.get(
                "payment_method", Payment.PaymentMethod.CASH
            ),
            actor=getattr(request, "user", None),
        )

    def validate(self, attrs):
        # A friendly pre-check against the unlocked row; the service re-runs
        # the same rules under select_for_update and is the authority.
        amount_paid = attrs["amount_paid"]
        fee = attrs["fee"]

        if amount_paid <= 0:
            raise serializers.ValidationError(
                {"amount_paid": f"Amount must be greater than 0 for Fee ID {fee.id}."}
            )

        if fee.status == Fee.FeeStatus.WAIVED:
            raise serializers.ValidationError(
                {
                    "fee": (
                        f"Fee ID {fee.id} is waived and cannot receive "
                        "payments. Unwaive it first."
                    )
                }
            )

        if amount_paid > fee.balance:
            raise serializers.ValidationError(
                {
                    "amount_paid": (
                        f"Amount {amount_paid} exceeds remaining balance "
                        f"{fee.balance} for Fee ID {fee.id}."
                    )
                }
            )

        return attrs


class PaymentCancelSerializer(serializers.Serializer):
    """Input for POST /payments/{id}/cancel/."""

    reason = serializers.CharField(required=True, allow_blank=False)


class PaymentReadSerializer(serializers.ModelSerializer):

    fee = FeeReadSlimSerializer()
    received_by = serializers.StringRelatedField()
    cancelled_by = serializers.StringRelatedField()

    class Meta:
        model = Payment
        fields = "__all__"


class PaymentReadSlimSerializer(serializers.ModelSerializer):

    fee = FeeReadMiniSerializer()
    received_by = serializers.CharField(source="received_by.username")
    payment_submission = serializers.StringRelatedField()
    cancelled_by = serializers.StringRelatedField()

    class Meta:
        model = Payment
        fields = "__all__"


class PaymentReadMiniSerializer(serializers.ModelSerializer):

    fee = FeeReadMiniSerializer()
    received_by = serializers.CharField(source="received_by.username")

    class Meta:
        model = Payment
        fields = [
            "id",
            "fee",
            "received_by",
            "amount_paid",
            "payment_method",
            "status",
            "updated_at",
        ]


class FeePaymentsReadMiniSerializer(serializers.ModelSerializer):

    received_by = serializers.CharField(source="received_by.username")
    cancelled_by = serializers.StringRelatedField()

    class Meta:
        model = Payment
        fields = [
            "id",
            "received_by",
            "amount_paid",
            "previous_balance",
            "payment_method",
            "status",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "updated_at",
            "created_at",
        ]


class PaymentDeleteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id"]


class PaymentBulkCreateSerializer(serializers.Serializer):
    payments = PaymentCreateSerializer(many=True)

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        user = request.user

        # Each record_payment call re-reads its fee under lock, so two items
        # for the same fee each deduct from the balance the previous one left
        # behind - and the whole batch rolls back if any item cannot pay.
        return [
            payment_services.record_payment(
                fee=item["fee"],
                amount=item["amount_paid"],
                method=item.get("payment_method", Payment.PaymentMethod.CASH),
                actor=user,
            )
            for item in validated_data["payments"]
        ]
