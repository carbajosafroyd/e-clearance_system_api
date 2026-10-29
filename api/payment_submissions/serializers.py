import re
from rest_framework import serializers
from api.utils.cloudinary.serializers import CloudinaryUploadMixin
from api.utils.cloudinary.cloudinary_utils import get_file_url
from api.utils.cloudinary.paths import Kind, folder_for, tags_for
from .models import PaymentSubmission, PaymentSubmissionItems
from typing import List
from ..fees.models import Fee
from api.students.serializers import StudentReadMiniSerializer
from api.fees.serializers import FeeReadMiniV2Serializer


class PaymentSubmissionItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentSubmissionItems
        fields = ["fee", "amount_paid"]

    def validate(self, attrs):
        fee = attrs["fee"]
        amount = attrs["amount_paid"]

        if fee.status == Fee.FeeStatus.PAID:
            raise serializers.ValidationError(
                {"fee": "Fee status is already paid, can't make payments"}
            )

        if fee.status == Fee.FeeStatus.WAIVED:
            raise serializers.ValidationError(
                {"fee": "Fee is waived - it does not accept payments."}
            )

        if amount > fee.balance:
            raise serializers.ValidationError(
                f"Amount {amount} exceeds remaining balance of {fee.balance} for fee '{fee}'"
            )

        if amount <= 0:
            raise serializers.ValidationError("Amount must be greater than 0")

        return attrs


class PaymentReadSlimItemSerializer(serializers.ModelSerializer):
    fee = FeeReadMiniV2Serializer()
    status = serializers.SerializerMethodField()

    class Meta:
        model = PaymentSubmissionItems
        fields = ["id", "fee", "previous_balance", "amount_paid", "status"]

    def get_status(self, obj) -> str:
        # Items carry no status of their own - an item is cancelled exactly
        # when its submission is, so the parent's status is echoed here.
        if (
            obj.payment_submission.status
            == PaymentSubmission.SubmissionStatus.CANCELLED
        ):
            return "cancelled"
        return "active"


class PaymentSubmissionCreateSerializer(
    CloudinaryUploadMixin, serializers.ModelSerializer
):
    items = PaymentSubmissionItemSerializer(many=True, write_only=True)
    # Declared rather than derived from the model. ModelSerializer would give
    # this max_length=11 from the column, and that check runs *before*
    # validate_gcash_number - so "+63 917 123 4567" was rejected on length
    # before it ever reached the normaliser that knows how to read it.
    gcash_number = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=20
    )
    screenshot_urls = serializers.SerializerMethodField()
    items_created = PaymentSubmissionItemSerializer(
        source="payment_submission_list",
        many=True,
        read_only=True
    )
    class Meta:
        model = PaymentSubmission
        fields = [
            # "student",
            # "fee",
            "total_amount_paid",
            "reference_number",
            "gcash_number",
            # "screenshot",
            "images",
            # "folder",
            "screenshot_urls",
            "remarks",
            "items",
            "items_created",
        ]

    def get_screenshot_urls(self, obj) -> List[str]:
        return [get_file_url(pid, version=obj.cloudinary_version) for pid in (obj.screenshot or [])]

    def validate_gcash_number(self, value: str) -> str:
        """Normalise to the 11-digit local form the column is sized for.

        The frontend fills this in from the picked account or from its build
        config - a student never types it - but the API is still the boundary,
        and a deployment's configured number can legitimately arrive written
        as +63 917 123 4567. Rewriting that here beats rejecting a payment
        somebody has already sent.
        """
        if value in (None, ""):
            return value

        digits = re.sub(r"[^0-9+]", "", str(value))
        if digits.startswith("+63"):
            digits = "0" + digits[3:]
        elif digits.startswith("63") and len(digits) == 12:
            digits = "0" + digits[2:]
        digits = digits.lstrip("+")

        if not (len(digits) == 11 and digits.startswith("09") and digits.isdigit()):
            raise serializers.ValidationError(
                "Expected an 11-digit GCash number starting with 09."
            )
        return digits

    def create(self, validated_data):

        images = validated_data.pop("images", [])
        items_data = validated_data.pop("items", [])
        validated_data["student"] = self.context["request"].user.student

        instance = super().create(validated_data)
        for item_data in items_data:
            # crete a validation wherein fee to pay should be his only
            # get the fee and have the previous balance.
            item_data["previous_balance"] = item_data["fee"].balance
            PaymentSubmissionItems.objects.create(
                payment_submission=instance, **item_data
            )

        # Filed under the student who sent it - see paths.py for the tree.
        instance.folder = folder_for(instance.student, Kind.PAYMENTS)

        self.handle_cloudinary_upload(
            instance, images, field_name="screenshot", tags=tags_for(instance.student)
        )

        return instance

    def validate(self, attrs):
        # add validations here for katong mga list of fees
        # fee = attrs["fee"]
        # try:
        #     fee = Fee.objects.get(pk=attrs["fee"].pk)
        # except Fee.DoesNotExist:
        #     raise serializers.ValidationError({"fee": "Invalid fee ID"})

        # CHECK HERE ALSO IF ANG FEE IS FOR THE STUDENT CURRENTLY LOGGED IN :>
        items = attrs.get("items")
        
        user = self.context[
            "request"
        ].user  # in case permission fails or will be removed by future devs. I still need the user student though. Ill keep this
        if not hasattr(user, "student"):
            raise serializers.ValidationError(
                "Student users are only eligible to create payment submissions"
            )

        if not items:
            raise serializers.ValidationError("Fee Items Shoudld be >= 1")

        for item in items:
            fee = item["fee"]
            if fee.student != user.student:
                raise serializers.ValidationError(
                    f"Fee id:{fee} does not belong to the current user."
                )

        if not attrs["images"]:
            raise serializers.ValidationError(
                {"images": "At least 1 image is required"}
            )

        total_from_items = sum(item["amount_paid"] for item in items)
        if total_from_items != attrs.get("total_amount_paid", 0):
            raise serializers.ValidationError(
                f"Total amount paid ({attrs.get('total_amount_paid')}) does not match sum of items ({total_from_items})"
            )
        return attrs


class PaymentSubmissionReadSerializer(serializers.ModelSerializer):
    student = StudentReadMiniSerializer()
    # fee = FeeReadSlimSerializer()
    screenshot_urls = serializers.SerializerMethodField()
    reviewed_by = serializers.StringRelatedField()
    cancelled_by = serializers.StringRelatedField()
    fee_items = PaymentReadSlimItemSerializer(
        source="payment_submission_list", many=True, read_only=True
    )

    class Meta:
        model = PaymentSubmission
        fields = "__all__"

    def get_screenshot_urls(self, obj) -> List[str]:
        return [get_file_url(pid, version=obj.cloudinary_version) for pid in (obj.screenshot or [])]


class PaymentSubmissionReadSlimSerializer(serializers.ModelSerializer):
    student = StudentReadMiniSerializer()
    # fee = FeeReadMiniSerializer()
    screenshot_urls = serializers.SerializerMethodField()
    reviewed_by = serializers.StringRelatedField()
    fee_payment_count = serializers.SerializerMethodField()

    class Meta:
        model = PaymentSubmission
        fields = [
            "id",
            "student",  # removed since naa nay student sa fee :> gi balik since ang fee gi hawa. (need for bulk creation of submission)
            # "fee",
            "screenshot_urls",
            "reviewed_by",
            "total_amount_paid",
            "reference_number",
            "gcash_number",
            "status",
            "fee_payment_count",
            "created_at",
        ]

    def get_screenshot_urls(self, obj) -> List[str]:
        return [get_file_url(pid, version=obj.cloudinary_version) for pid in (obj.screenshot or [])]

    def get_fee_payment_count(self, obj) -> int:
        # Goes through the related manager (not a fresh filter) so the view's
        # prefetch cache answers this without a COUNT query per row.
        return obj.payment_submission_list.count()


class PaymentSubmissionUpdateSerializer(serializers.ModelSerializer):
    """
    The only thing an officer may change on a submission after the student
    sent it: their own remarks.

    The reference number, the receipt images, the amount and the fee
    allocations are the student's claim - the evidence an approval was judged
    on. Rewriting them would leave a record that says something the student
    never said, so the proof is immutable: a wrong submission is rejected (or
    an approved one cancelled) with a reason, and the student sends a correct
    one. A field that tries to move anyway is refused rather than silently
    dropped, so an API caller learns the rule instead of assuming it worked.
    """

    #: Everything a submission carries that is not the officer's note.
    LOCKED_FIELDS = (
        "reference_number",
        "images",
        "screenshot",
        "image_urls",
        "gcash_number",
        "total_amount_paid",
        "items",
        "status",
        "student",
    )

    class Meta:
        model = PaymentSubmission
        fields = ["remarks"]

    def validate(self, attrs):
        data = getattr(self, "initial_data", None) or {}
        locked = [name for name in self.LOCKED_FIELDS if name in data]
        if locked:
            raise serializers.ValidationError(
                {
                    "detail": (
                        "A submission's proof cannot be edited once it is sent "
                        f"({', '.join(locked)}). Reject it - or cancel it if it "
                        "was approved - with a reason, and ask the student to "
                        "submit again. Only the remarks can be changed here."
                    )
                }
            )
        return attrs


class PaymentSubmissionDeleteSerializer(serializers.ModelSerializer):
    confirm = serializers.BooleanField(write_only=True, required=True)

    class Meta:
        model = PaymentSubmission
        fields = ["id", "confirm"]

    def validate_confirm(self, value):
        if not value:
            raise serializers.ValidationError("You must confirm deletion.")
        return value


class RemarksSubmissionSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False)
