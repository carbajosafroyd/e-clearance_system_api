"""Serializers for student complaints."""

from __future__ import annotations

from rest_framework import serializers

from api.fees.models import Fee
from api.students.models import Student

from .models import FEE_RELATED_CATEGORIES, Complaint


class StudentBriefSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    program = serializers.CharField(source="program.acronym", read_only=True)

    class Meta:
        model = Student
        fields = ["id", "s_studentID", "full_name", "program", "s_lvl"]

    def get_full_name(self, obj) -> str:
        parts = [obj.s_fname, obj.s_mname, obj.s_lname, obj.s_suffix]
        return " ".join(part for part in parts if part)


class ComplaintFeeSerializer(serializers.ModelSerializer):
    """Just enough of the disputed fee to act on it without opening it."""

    category_name = serializers.CharField(
        source="category.category_name", read_only=True
    )

    class Meta:
        model = Fee
        fields = ["id", "category_name", "total_amount", "balance", "status"]


class ComplaintReadSerializer(serializers.ModelSerializer):
    student = StudentBriefSerializer(read_only=True)
    related_fee = ComplaintFeeSerializer(read_only=True)
    system_label = serializers.CharField(source="get_system_display", read_only=True)
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    handled_by_username = serializers.CharField(
        source="handled_by.username", read_only=True, default=None
    )

    class Meta:
        model = Complaint
        fields = [
            "id",
            "student",
            "system",
            "system_label",
            "category",
            "category_label",
            "related_fee",
            "title",
            "description",
            "attachments",
            "status",
            "status_label",
            "response",
            "handled_by",
            "handled_by_username",
            "handled_at",
            "created_at",
            "updated_at",
        ]


class ComplaintSlimSerializer(serializers.ModelSerializer):
    """List rows: enough to triage, not enough to make the page heavy."""

    student_name = serializers.SerializerMethodField()
    system_label = serializers.CharField(source="get_system_display", read_only=True)
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Complaint
        fields = [
            "id",
            "student_name",
            "system",
            "system_label",
            "category",
            "category_label",
            "related_fee",
            "title",
            "status",
            "status_label",
            "created_at",
            "handled_at",
        ]

    def get_student_name(self, obj) -> str:
        return f"{obj.student.s_fname} {obj.student.s_lname}".strip()


class ComplaintWriteSerializer(serializers.ModelSerializer):
    """Filing one, and editing it while nobody has answered.

    `status`, `response` and the handled_* fields are absent on purpose: a
    student cannot mark their own complaint resolved, and an officer changes
    it through the resolve/reject actions, which are the ones that notify.
    """

    class Meta:
        model = Complaint
        fields = [
            "id",
            "system",
            "category",
            "related_fee",
            "title",
            "description",
            "attachments",
        ]

    def validate_title(self, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise serializers.ValidationError(
                "Give it a title somebody can triage from, not two words."
            )
        return value

    def validate_description(self, value: str) -> str:
        value = value.strip()
        if len(value) < 20:
            raise serializers.ValidationError(
                "Say what happened, when, and what you expected instead - a "
                "line is not enough for anyone to act on."
            )
        return value

    def validate(self, attrs):
        """A student may only complain about their own fee.

        The row scoping in `get_queryset` covers reading; this covers
        writing, where the id arrives from the client and nothing else would
        stop it pointing at a stranger's fee.
        """
        fee = attrs.get("related_fee")
        student = getattr(self.instance, "student", None) or getattr(
            self.context.get("request").user, "student", None
        )

        if fee is not None and student is not None and fee.student_id != student.pk:
            raise serializers.ValidationError(
                {"related_fee": "That fee is not yours."}
            )

        category = attrs.get("category") or getattr(self.instance, "category", None)
        if category in FEE_RELATED_CATEGORIES and fee is None and self.instance is None:
            # A nudge, not a refusal: a student who cannot find the row still
            # deserves to be heard, and staff can attach it later.
            self.context["fee_hint"] = (
                "Naming the fee makes this much faster to settle."
            )
        return attrs


class ComplaintResponseSerializer(serializers.Serializer):
    """The answer that closes a complaint. Never optional."""

    response = serializers.CharField(
        allow_blank=False,
        help_text="Shown to the student, in full, in the notification.",
    )


class ComplaintWithdrawSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)


class ComplaintSummarySerializer(serializers.Serializer):
    """Dashboard figures for the complaints queue."""

    total = serializers.IntegerField()
    open = serializers.IntegerField()
    submitted = serializers.IntegerField()
    acknowledged = serializers.IntegerField()
    resolved = serializers.IntegerField()
    rejected = serializers.IntegerField()
    withdrawn = serializers.IntegerField()
    by_system = serializers.ListField(child=serializers.DictField())
    by_category = serializers.ListField(child=serializers.DictField())
