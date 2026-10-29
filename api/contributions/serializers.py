"""Serializers for contributions, points and fee discounts."""

from __future__ import annotations

from rest_framework import serializers

from api.students.models import Student
from core.choices import SystemScope

from .models import (
    Contribution,
    ContributionImpact,
    ContributionKind,
    FeeDiscount,
    PointAccount,
    PointPolicy,
    PointTransaction,
)
from .services import suggested_award


class StudentBriefSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    program = serializers.CharField(source="program.acronym", read_only=True)

    class Meta:
        model = Student
        fields = ["id", "s_studentID", "full_name", "program", "s_lvl"]

    def get_full_name(self, obj) -> str:
        parts = [obj.s_fname, obj.s_mname, obj.s_lname, obj.s_suffix]
        return " ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Contributions
# ---------------------------------------------------------------------------


class ContributionReadSerializer(serializers.ModelSerializer):
    student = StudentBriefSerializer(read_only=True)
    system_label = serializers.CharField(source="get_system_display", read_only=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    reviewed_by_username = serializers.CharField(
        source="reviewed_by.username", read_only=True, default=None
    )
    #: What the policy would pay if this were approved right now. Lets a
    #: reviewer see the number before committing to it.
    suggested_points = serializers.SerializerMethodField()

    class Meta:
        model = Contribution
        fields = [
            "id",
            "student",
            "system",
            "system_label",
            "kind",
            "kind_label",
            "impact",
            "title",
            "description",
            "attachments",
            "status",
            "status_label",
            "points_awarded",
            "suggested_points",
            "reviewed_by",
            "reviewed_by_username",
            "reviewed_at",
            "review_remarks",
            "created_at",
            "updated_at",
        ]

    def get_suggested_points(self, obj) -> int:
        if obj.status in Contribution.TERMINAL_STATUSES:
            return obj.points_awarded
        return suggested_award(obj)


class ContributionSlimSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    system_label = serializers.CharField(source="get_system_display", read_only=True)

    class Meta:
        model = Contribution
        fields = [
            "id",
            "student",
            "student_name",
            "system",
            "system_label",
            "kind",
            "impact",
            "title",
            "status",
            "points_awarded",
            "created_at",
            "updated_at",
        ]

    def get_student_name(self, obj) -> str:
        parts = [obj.student.s_fname, obj.student.s_lname, obj.student.s_suffix]
        return " ".join(part for part in parts if part)


class ContributionWriteSerializer(serializers.ModelSerializer):
    """Student-facing create/update.

    `student`, `status` and `points_awarded` are not writable: the student is
    taken from the request, and the other two only move through the review
    actions.
    """

    class Meta:
        model = Contribution
        fields = ["id", "system", "kind", "impact", "title", "description", "attachments"]

    def validate_title(self, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise serializers.ValidationError(
                "Give it a title somebody can triage from - at least 8 characters."
            )
        return value

    def validate_description(self, value: str) -> str:
        value = value.strip()
        if len(value) < 20:
            raise serializers.ValidationError(
                "Describe what happened and what you expected instead - at "
                "least 20 characters."
            )
        return value


class ContributionReviewSerializer(serializers.Serializer):
    """Payload for approve."""

    points = serializers.IntegerField(
        required=False,
        min_value=0,
        help_text="Override the policy's suggestion. Omit to use it.",
    )
    impact = serializers.ChoiceField(
        choices=ContributionImpact.choices,
        required=False,
        help_text="Correct the student's impact rating before scoring.",
    )
    remarks = serializers.CharField(required=False, allow_blank=True)


class ContributionRejectSerializer(serializers.Serializer):
    remarks = serializers.CharField()
    duplicate = serializers.BooleanField(
        default=False,
        help_text="Record it as a duplicate rather than a rejection.",
    )


class ContributionRevokeSerializer(serializers.Serializer):
    remarks = serializers.CharField()


# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------


class PointAccountSerializer(serializers.ModelSerializer):
    student = StudentBriefSerializer(read_only=True)
    system_label = serializers.CharField(source="get_system_display", read_only=True)

    class Meta:
        model = PointAccount
        fields = [
            "id",
            "student",
            "system",
            "system_label",
            "total_earned",
            "total_spent",
            "balance",
            "updated_at",
        ]


class PointTransactionSerializer(serializers.ModelSerializer):
    system = serializers.CharField(source="account.system", read_only=True)
    reason_label = serializers.CharField(source="get_reason_display", read_only=True)
    contribution_title = serializers.CharField(
        source="contribution.title", read_only=True, default=None
    )

    class Meta:
        model = PointTransaction
        fields = [
            "id",
            "system",
            "delta",
            "balance_after",
            "reason",
            "reason_label",
            "remarks",
            "contribution",
            "contribution_title",
            "fee_discount",
            "created_at",
        ]


class WalletEntrySerializer(serializers.Serializer):
    system = serializers.CharField()
    label = serializers.CharField()
    balance = serializers.IntegerField()
    total_earned = serializers.IntegerField()
    total_spent = serializers.IntegerField()


class WalletSerializer(serializers.Serializer):
    wallets = WalletEntrySerializer(many=True)
    total_balance = serializers.IntegerField()
    total_earned = serializers.IntegerField()
    total_spent = serializers.IntegerField()
    approved_contributions = serializers.IntegerField()
    pending_contributions = serializers.IntegerField()
    discount_total = serializers.DecimalField(max_digits=14, decimal_places=2)
    rank = serializers.IntegerField(allow_null=True)


class LeaderboardEntrySerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    student_id = serializers.IntegerField()
    student_school_id = serializers.CharField()
    student_name = serializers.CharField()
    program = serializers.CharField(allow_null=True)
    system = serializers.CharField()
    points_earned = serializers.IntegerField()
    points_spent = serializers.IntegerField()
    points_balance = serializers.IntegerField()
    approved_contributions = serializers.IntegerField()


class AdjustPointsSerializer(serializers.Serializer):
    student = serializers.PrimaryKeyRelatedField(queryset=Student.objects.all())
    system = serializers.ChoiceField(choices=SystemScope.choices)
    delta = serializers.IntegerField()
    remarks = serializers.CharField()

    def validate_delta(self, value: int) -> int:
        if value == 0:
            raise serializers.ValidationError("An adjustment of zero does nothing.")
        return value


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


class PointPolicySerializer(serializers.ModelSerializer):
    system_label = serializers.CharField(source="get_system_display", read_only=True)

    class Meta:
        model = PointPolicy
        fields = [
            "id",
            "institute",
            "system",
            "system_label",
            "awards",
            "peso_per_point",
            "min_points_per_redemption",
            "max_points_per_redemption",
            "max_discount_percent",
            "is_active",
            "created_at",
            "updated_at",
        ]

    def validate_awards(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                'Expected a mapping of kind to points, e.g. {"bug_report": 30}.'
            )

        valid = {str(kind) for kind, _ in ContributionKind.choices}
        for key, points in value.items():
            if key not in valid:
                raise serializers.ValidationError(
                    f"'{key}' is not a contribution kind. Valid: {', '.join(sorted(valid))}."
                )
            if not isinstance(points, int) or points < 0:
                raise serializers.ValidationError(
                    f"Award for '{key}' must be a non-negative whole number."
                )
        return value

    def validate(self, attrs):
        minimum = attrs.get(
            "min_points_per_redemption",
            getattr(self.instance, "min_points_per_redemption", 0),
        )
        maximum = attrs.get(
            "max_points_per_redemption",
            getattr(self.instance, "max_points_per_redemption", 0),
        )
        if maximum < minimum:
            raise serializers.ValidationError(
                {
                    "max_points_per_redemption": (
                        "The maximum a student may spend cannot be below the "
                        "minimum."
                    )
                }
            )
        return attrs


# ---------------------------------------------------------------------------
# Fee discounts
# ---------------------------------------------------------------------------


class FeeDiscountReadSerializer(serializers.ModelSerializer):
    student = StudentBriefSerializer(read_only=True)
    system_label = serializers.CharField(source="get_system_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    fee_category = serializers.CharField(
        source="fee.category.category_name", read_only=True
    )
    requested_by_username = serializers.CharField(
        source="requested_by.username", read_only=True, default=None
    )
    reviewed_by_username = serializers.CharField(
        source="reviewed_by.username", read_only=True, default=None
    )
    cancelled_by_username = serializers.CharField(
        source="cancelled_by.username", read_only=True, default=None
    )

    class Meta:
        model = FeeDiscount
        fields = [
            "id",
            "fee",
            "fee_category",
            "student",
            "system",
            "system_label",
            "points_spent",
            "amount",
            "status",
            "status_label",
            "previous_total_amount",
            "previous_balance",
            "requested_by",
            "requested_by_username",
            "reviewed_by",
            "reviewed_by_username",
            "reviewed_at",
            "remarks",
            "cancelled_by",
            "cancelled_by_username",
            "cancelled_at",
            "cancellation_reason",
            "created_at",
            "updated_at",
        ]


class FeeDiscountRequestSerializer(serializers.Serializer):
    """A student asking to spend points against one of their fees."""

    fee = serializers.IntegerField()
    system = serializers.ChoiceField(choices=SystemScope.choices)
    points = serializers.IntegerField(min_value=1)
    remarks = serializers.CharField(required=False, allow_blank=True)


class DiscountQuoteRequestSerializer(serializers.Serializer):
    fee = serializers.IntegerField()
    system = serializers.ChoiceField(choices=SystemScope.choices)
    points = serializers.IntegerField(min_value=0, default=0)


class DiscountQuoteSerializer(serializers.Serializer):
    """What the student would get, so the UI can show it before they commit."""

    system = serializers.CharField()
    points = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    available_points = serializers.IntegerField()
    peso_per_point = serializers.DecimalField(max_digits=8, decimal_places=2)
    min_points = serializers.IntegerField()
    max_points = serializers.IntegerField()
    max_discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2)
    max_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    policy_active = serializers.BooleanField()


class RemarksSerializer(serializers.Serializer):
    remarks = serializers.CharField()


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField()


class OptionalRemarksSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Summary / report
# ---------------------------------------------------------------------------


class SystemBreakdownSerializer(serializers.Serializer):
    system = serializers.CharField()
    label = serializers.CharField()
    total = serializers.IntegerField()
    approved = serializers.IntegerField()
    pending = serializers.IntegerField()
    points_awarded = serializers.IntegerField()
    discount_count = serializers.IntegerField()
    discount_amount = serializers.DecimalField(max_digits=14, decimal_places=2)


class KindBreakdownSerializer(serializers.Serializer):
    kind = serializers.CharField()
    total = serializers.IntegerField()
    approved = serializers.IntegerField()
    points = serializers.IntegerField()


class ContributionStatusCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    pending = serializers.IntegerField()
    under_review = serializers.IntegerField()
    approved = serializers.IntegerField()
    rejected = serializers.IntegerField()
    duplicate = serializers.IntegerField()


class PointTotalsSerializer(serializers.Serializer):
    earned = serializers.IntegerField()
    spent = serializers.IntegerField()
    outstanding = serializers.IntegerField()


class DiscountTotalsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    amount_granted = serializers.DecimalField(max_digits=14, decimal_places=2)
    amount_pending = serializers.DecimalField(max_digits=14, decimal_places=2)
    amount_cancelled = serializers.DecimalField(max_digits=14, decimal_places=2)
    students_helped = serializers.IntegerField()


class ContributionSummarySerializer(serializers.Serializer):
    system = serializers.CharField()
    contributions = ContributionStatusCountsSerializer()
    points = PointTotalsSerializer()
    discounts = DiscountTotalsSerializer()
    by_system = SystemBreakdownSerializer(many=True)
    by_kind = KindBreakdownSerializer(many=True)
