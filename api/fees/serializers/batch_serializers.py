from django.utils import timezone
from rest_framework import serializers

from api.collection_categories.models import CollectionCategory
from api.institutes_attendance_events.models import InstituteAttendanceEvent

from ..constants import (
    MAX_YEAR_LEVEL,
    MIN_YEAR_LEVEL,
    SPECIAL_CATEGORY_NAMES,
)
from ..models import GeneratedFeeBatch


class GeneratedFeeBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeneratedFeeBatch
        fields = "__all__"


class ReadGeneratedFeeBatch(serializers.ModelSerializer):
    category = serializers.StringRelatedField(read_only=True)
    generated_by = serializers.StringRelatedField(read_only=True)
    updated_by = serializers.StringRelatedField(read_only=True)
    institute = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = GeneratedFeeBatch
        fields = "__all__"


class DeleteGeneratedFeeBtach(serializers.ModelSerializer):
    class Meta:
        model = GeneratedFeeBatch
        fields = ["id"]


class StudentScopeMixin:
    """
    Shared validation for the two generation payloads.

    Both flows offer the same student-targeting knobs, and this used to be
    copy-pasted into each serializer *and* re-checked in the viewset. One
    implementation, checked once, at the serializer boundary.
    """

    def validate(self, attrs):
        attrs = super().validate(attrs)  # type: ignore[misc]

        if attrs.get("all_students", True):
            return attrs

        year_levels = attrs.get("year_levels") or []
        if not year_levels:
            raise serializers.ValidationError(
                {
                    "year_levels": (
                        "Provide at least one year level when all_students is False."
                    )
                }
            )
        if any(
            level < MIN_YEAR_LEVEL or level > MAX_YEAR_LEVEL for level in year_levels
        ):
            raise serializers.ValidationError(
                {
                    "year_levels": (
                        f"Year levels must be between {MIN_YEAR_LEVEL} and "
                        f"{MAX_YEAR_LEVEL}."
                    )
                }
            )
        return attrs

    def validate_due_date(self, value):
        if value < timezone.now():
            raise serializers.ValidationError("Due date cannot be in the past.")
        return value


class GenerateFees(StudentScopeMixin, serializers.ModelSerializer):
    category_name = serializers.CharField()
    # Wire name kept for frontend compatibility. It reads as "exempt these
    # students", but it actually means "honour the FeeExemptedStudent rows for
    # this category" - the engine calls it apply_exemptions.
    exempt_students = serializers.BooleanField(default=False)
    all_students = serializers.BooleanField(default=True)
    year_levels = serializers.ListField(
        child=serializers.IntegerField(), required=False
    )
    due_date = serializers.DateTimeField()

    class Meta:
        model = GeneratedFeeBatch
        fields = [
            "category_name",
            "exempt_students",
            "academic_year",
            "semester",
            "all_students",
            "year_levels",
            "due_date",
        ]

    def validate_category_name(self, value):
        """Resolves the name to the CollectionCategory inside the caller's
        institute, so the viewset never has to re-look-it-up or re-check
        tenancy."""
        if value in SPECIAL_CATEGORY_NAMES:
            raise serializers.ValidationError(
                f"'{value}' has its own generation flow and is not available here."
            )

        user = self.context["request"].user
        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            raise serializers.ValidationError(
                "Your account is not attached to an institute."
            )

        try:
            return CollectionCategory.objects.get(
                category_name=value, institute_id=institute_id
            )
        except CollectionCategory.DoesNotExist:
            raise serializers.ValidationError(
                f"Category '{value}' does not exist or is outside your institute scope."
            )


class GenerateAttendanceFees(StudentScopeMixin, serializers.ModelSerializer):
    # Attendance can't be keyed on the category alone - one institute runs many
    # attendance events per term, each billed separately - so the event is the
    # scope for this flow.
    institute_attendance_event_id = serializers.IntegerField(write_only=True)
    exempt_students = serializers.BooleanField(default=False)
    all_students = serializers.BooleanField(default=True)
    year_levels = serializers.ListField(
        child=serializers.IntegerField(), required=False
    )
    due_date = serializers.DateTimeField()

    class Meta:
        model = GeneratedFeeBatch
        fields = [
            "institute_attendance_event_id",
            "exempt_students",
            "all_students",
            "year_levels",
            "due_date",
        ]

    def validate_institute_attendance_event_id(self, value):
        user = self.context["request"].user
        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            raise serializers.ValidationError(
                "Your account is not attached to an institute."
            )

        try:
            return InstituteAttendanceEvent.objects.get(
                id=value, institute_id=institute_id
            )
        except InstituteAttendanceEvent.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid attendance event, or the event is outside your institute."
            )


class GenerationAcceptedSerializer(serializers.Serializer):
    """202 body for both generation endpoints."""

    detail = serializers.CharField()
