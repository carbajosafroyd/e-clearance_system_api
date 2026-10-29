from django.contrib.auth.models import Group
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from .models import (
    ClearanceType,
    ClearanceRequirement,
    StudentClearance,
    StudentClearanceItem,
)


def build_full_name(first_name, middle_name, last_name, suffix=None):
    parts = [
        (first_name or "").strip(),
        (middle_name or "").strip(),
        (last_name or "").strip(),
        (suffix or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def get_program_display(program):
    if not program:
        return ""

    for attr in ["acronym", "prog_abbr", "program_name", "name"]:
        value = getattr(program, attr, None)
        if value:
            return str(value)

    return str(program)


class ClearanceRoleSlimSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ["id", "name"]

    def validate_name(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Role name cannot be empty.")

        queryset = Group.objects.filter(name__iexact=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError("A role with this name already exists.")

        return value


def get_requirement_authorizing_roles(requirement):
    roles = []

    prefetched = getattr(requirement, "_prefetched_objects_cache", {})
    if "authorizing_roles" in prefetched:
        roles = list(prefetched["authorizing_roles"])
    elif getattr(requirement, "pk", None):
        roles = list(requirement.authorizing_roles.all())

    if not roles and getattr(requirement, "authorizing_role", None):
        roles = [requirement.authorizing_role]

    unique_roles = []
    seen_ids = set()
    for role in roles:
        if role and role.id not in seen_ids:
            unique_roles.append(role)
            seen_ids.add(role.id)

    return unique_roles


class ClearanceRequirementReadSerializer(serializers.ModelSerializer):
    clearance_type_id = serializers.IntegerField(read_only=True)
    clearance_type_name = serializers.CharField(source="clearance_type.name", read_only=True)
    authorizing_role_id = serializers.IntegerField(read_only=True)
    authorizing_role_name = serializers.CharField(source="authorizing_role.name", read_only=True)
    authorizing_roles = serializers.SerializerMethodField()
    authorizing_role_ids = serializers.SerializerMethodField()
    authorizing_role_names = serializers.SerializerMethodField()

    class Meta:
        model = ClearanceRequirement
        fields = [
            "id",
            "clearance_type_id",
            "clearance_type_name",
            "name",
            "authorizing_role_id",
            "authorizing_role_name",
            "authorizing_roles",
            "authorizing_role_ids",
            "authorizing_role_names",
            "order_sequence",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(ClearanceRoleSlimSerializer(many=True))
    def get_authorizing_roles(self, obj):
        return ClearanceRoleSlimSerializer(get_requirement_authorizing_roles(obj), many=True).data

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_authorizing_role_ids(self, obj):
        return [role.id for role in get_requirement_authorizing_roles(obj)]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_authorizing_role_names(self, obj):
        return [role.name for role in get_requirement_authorizing_roles(obj)]


class ClearanceRequirementWriteSerializer(serializers.ModelSerializer):
    authorizing_role = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(),
        required=False,
        allow_null=True,
    )
    authorizing_role_ids = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(),
        many=True,
        write_only=True,
        required=False,
    )

    class Meta:
        model = ClearanceRequirement
        fields = [
            "id",
            "clearance_type",
            "name",
            "authorizing_role",
            "authorizing_role_ids",
            "order_sequence",
        ]
        read_only_fields = ["id"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Requirement name cannot be empty.")
        return value

    def validate(self, attrs):
        clearance_type = attrs.get("clearance_type") or getattr(self.instance, "clearance_type", None)

        if clearance_type and clearance_type.is_issued:
            raise serializers.ValidationError(
                {"clearance_type": "Issued clearance requirements can no longer be modified."}
            )

        role_ids_provided = "authorizing_role_ids" in attrs
        authorizing_roles = attrs.get("authorizing_role_ids") or []
        authorizing_role = attrs.get("authorizing_role") or getattr(self.instance, "authorizing_role", None)

        if role_ids_provided and not authorizing_roles:
            raise serializers.ValidationError(
                {"authorizing_role_ids": "Select at least one authorizing role."}
            )

        if not authorizing_role and not authorizing_roles:
            raise serializers.ValidationError(
                {"authorizing_role_ids": "Select at least one authorizing role."}
            )

        if not authorizing_role and authorizing_roles:
            attrs["authorizing_role"] = authorizing_roles[0]

        return attrs

    def create(self, validated_data):
        authorizing_roles = validated_data.pop("authorizing_role_ids", None)
        requirement = super().create(validated_data)
        requirement.authorizing_roles.set(authorizing_roles or [requirement.authorizing_role])
        return requirement

    def update(self, instance, validated_data):
        authorizing_roles = validated_data.pop("authorizing_role_ids", None)
        requirement = super().update(instance, validated_data)

        if authorizing_roles is not None:
            requirement.authorizing_roles.set(authorizing_roles)
        elif "authorizing_role" in validated_data:
            requirement.authorizing_roles.set([requirement.authorizing_role])

        return requirement


class ClearanceTypeSlimSerializer(serializers.ModelSerializer):
    requirement_count = serializers.SerializerMethodField()

    class Meta:
        model = ClearanceType
        fields = [
            "id",
            "name",
            "academic_year",
            "semester",
            "is_active",
            "target_year_levels",
            "is_issued",
            "issued_at",
            "requirement_count",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.IntegerField())
    def get_requirement_count(self, obj):
        annotated_count = getattr(obj, "requirement_count", None)
        if annotated_count is not None:
            return annotated_count

        prefetched = getattr(obj, "_prefetched_objects_cache", {})
        if "requirements" in prefetched:
            return len(prefetched["requirements"])

        return obj.requirements.count()


class ClearanceTypeReadSerializer(serializers.ModelSerializer):
    requirements = ClearanceRequirementReadSerializer(many=True, read_only=True)

    class Meta:
        model = ClearanceType
        fields = [
            "id",
            "name",
            "description",
            "academic_year",
            "semester",
            "is_active",
            "target_year_levels",
            "is_issued",
            "issued_at",
            "created_at",
            "updated_at",
            "requirements",
        ]
        read_only_fields = fields


class ClearanceTypeWriteSerializer(serializers.ModelSerializer):
    institute = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ClearanceType
        fields = [
            "id",
            "institute",
            "name",
            "description",
            "academic_year",
            "semester",
            "is_active",
            "target_year_levels",
            "is_issued",
            "issued_at",
        ]
        read_only_fields = ["id", "institute", "is_issued", "issued_at"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Clearance type name cannot be empty.")
        return value

    def validate_target_year_levels(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("target_year_levels must be a list.")

        cleaned = []
        for item in value:
            if not isinstance(item, int):
                raise serializers.ValidationError("Each target year level must be an integer.")
            if item <= 0:
                raise serializers.ValidationError("Year levels must be positive integers.")
            cleaned.append(item)

        return sorted(set(cleaned))


class StudentClearanceItemReadSerializer(serializers.ModelSerializer):
    student_clearance_id = serializers.IntegerField(read_only=True)
    requirement_id = serializers.IntegerField(read_only=True)
    requirement_name = serializers.CharField(source="requirement.name", read_only=True)
    authorizing_role_name = serializers.CharField(
        source="requirement.authorizing_role.name",
        read_only=True,
    )
    authorizing_role_ids = serializers.SerializerMethodField()
    authorizing_role_names = serializers.SerializerMethodField()
    authorizing_roles = serializers.SerializerMethodField()
    order_sequence = serializers.IntegerField(
        source="requirement.order_sequence",
        read_only=True,
    )
    is_actionable = serializers.SerializerMethodField()
    proof_attachment_url = serializers.SerializerMethodField()
    cleared_by_user_id = serializers.IntegerField(read_only=True)
    cleared_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StudentClearanceItem
        fields = [
            "id",
            "student_clearance_id",
            "requirement_id",
            "requirement_name",
            "authorizing_role_name",
            "authorizing_role_ids",
            "authorizing_role_names",
            "authorizing_roles",
            "order_sequence",
            "is_actionable",
            "status",
            "amount_due",
            "remarks",
            "proof_attachment",
            "proof_attachment_url",
            "cloudinary_version",
            "proof_notes",
            "proof_uploaded_at",
            "cleared_by_user_id",
            "cleared_by_name",
            "cleared_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_is_actionable(self, obj) -> bool:
        if obj.status == StudentClearanceItem.ItemStatus.CLEARED:
            return False
        if hasattr(obj, "has_uncleared_predecessor"):
            return not obj.has_uncleared_predecessor
        return not StudentClearanceItem.objects.filter(
            student_clearance=obj.student_clearance,
            requirement__order_sequence__lt=obj.requirement.order_sequence,
        ).exclude(status=StudentClearanceItem.ItemStatus.CLEARED).exists()

    @extend_schema_field(ClearanceRoleSlimSerializer(many=True))
    def get_authorizing_roles(self, obj):
        return ClearanceRoleSlimSerializer(get_requirement_authorizing_roles(obj.requirement), many=True).data

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_authorizing_role_ids(self, obj):
        return [role.id for role in get_requirement_authorizing_roles(obj.requirement)]

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_authorizing_role_names(self, obj):
        return [role.name for role in get_requirement_authorizing_roles(obj.requirement)]

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_proof_attachment_url(self, obj):
        if not obj.proof_attachment:
            return None
        from api.utils.cloudinary.cloudinary_utils import get_file_url
        try:
            return get_file_url(obj.proof_attachment, version=obj.cloudinary_version)
        except Exception:
            return None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_cleared_by_name(self, obj):
        user = obj.cleared_by_user
        if not user:
            return None

        full_name = build_full_name(
            getattr(user, "first_name", ""),
            "",
            getattr(user, "last_name", ""),
        )

        return full_name or getattr(user, "username", None) or getattr(user, "email", None)


class StudentClearanceItemWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentClearanceItem
        fields = [
            "id",
            "student_clearance",
            "requirement",
            "status",
            "amount_due",
            "remarks",
            "proof_attachment",
            "cloudinary_version",
            "proof_notes",
            "proof_uploaded_at",
            "cleared_by_user",
            "cleared_at",
        ]
        read_only_fields = [
            "id",
            "cleared_by_user",
            "cleared_at",
            "proof_attachment",
            "cloudinary_version",
            "proof_uploaded_at",
        ]

    def validate(self, attrs):
        student_clearance = attrs.get("student_clearance") or getattr(self.instance, "student_clearance", None)
        requirement = attrs.get("requirement") or getattr(self.instance, "requirement", None)

        if student_clearance and requirement:
            if requirement.clearance_type_id != student_clearance.clearance_type_id:
                raise serializers.ValidationError(
                    {
                        "requirement": (
                            "Requirement must belong to the same clearance type as the student clearance."
                        )
                    }
                )

        return attrs


class StudentClearanceSlimSerializer(serializers.ModelSerializer):
    student_id = serializers.IntegerField(read_only=True)
    student_name = serializers.SerializerMethodField()
    student_number = serializers.CharField(source="student.s_studentID", read_only=True)
    program = serializers.SerializerMethodField()
    year_level = serializers.IntegerField(source="student.s_lvl", read_only=True)
    s_set = serializers.CharField(source="student.s_set", read_only=True)
    clearance_type_id = serializers.IntegerField(read_only=True)
    clearance_type_name = serializers.CharField(source="clearance_type.name", read_only=True)
    academic_year = serializers.CharField(source="clearance_type.academic_year", read_only=True)
    semester = serializers.CharField(source="clearance_type.semester", read_only=True)
    enrolled_by_user_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = StudentClearance
        fields = [
            "id",
            "student_id",
            "student_name",
            "student_number",
            "program",
            "year_level",
            "s_set",
            "clearance_type_id",
            "clearance_type_name",
            "academic_year",
            "semester",
            "status",
            "verification_token",
            "date_initiated",
            "date_completed",
            "is_enrolled",
            "enrolled_at",
            "enrolled_by_user_id",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_student_name(self, obj):
        return build_full_name(
            obj.student.s_fname,
            obj.student.s_mname,
            obj.student.s_lname,
            obj.student.s_suffix,
        )

    @extend_schema_field(serializers.CharField())
    def get_program(self, obj):
        return get_program_display(obj.student.program)


class StudentClearanceReadSerializer(serializers.ModelSerializer):
    student_id = serializers.IntegerField(read_only=True)
    student_name = serializers.SerializerMethodField()
    student_number = serializers.CharField(source="student.s_studentID", read_only=True)
    program = serializers.SerializerMethodField()
    year_level = serializers.IntegerField(source="student.s_lvl", read_only=True)
    s_set = serializers.CharField(source="student.s_set", read_only=True)
    clearance_type_id = serializers.IntegerField(read_only=True)
    clearance_type_name = serializers.CharField(source="clearance_type.name", read_only=True)
    academic_year = serializers.CharField(source="clearance_type.academic_year", read_only=True)
    semester = serializers.CharField(source="clearance_type.semester", read_only=True)
    enrolled_by_user_id = serializers.IntegerField(read_only=True)
    items = StudentClearanceItemReadSerializer(many=True, read_only=True)

    class Meta:
        model = StudentClearance
        fields = [
            "id",
            "student_id",
            "student_name",
            "student_number",
            "program",
            "year_level",
            "s_set",
            "clearance_type_id",
            "clearance_type_name",
            "academic_year",
            "semester",
            "status",
            "verification_token",
            "date_initiated",
            "date_completed",
            "is_enrolled",
            "enrolled_at",
            "enrolled_by_user_id",
            "items",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_student_name(self, obj):
        return build_full_name(
            obj.student.s_fname,
            obj.student.s_mname,
            obj.student.s_lname,
            obj.student.s_suffix,
        )

    @extend_schema_field(serializers.CharField())
    def get_program(self, obj):
        return get_program_display(obj.student.program)


class StudentClearanceWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentClearance
        fields = [
            "id",
            "student",
            "clearance_type",
            "status",
            "date_completed",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        student = attrs.get("student") or getattr(self.instance, "student", None)
        clearance_type = attrs.get("clearance_type") or getattr(self.instance, "clearance_type", None)

        if student and clearance_type:
            if student.s_lvl not in (clearance_type.target_year_levels or []):
                raise serializers.ValidationError(
                    {
                        "student": (
                            "Student year level is not included in the target year levels for this clearance type."
                        )
                    }
                )

        return attrs