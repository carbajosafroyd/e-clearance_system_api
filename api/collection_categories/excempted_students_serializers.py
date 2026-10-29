from django.db import transaction
from rest_framework import serializers
from api.collection_categories.models import (
    CollectionCategory,
    FeeExemptedStudent,
)
from api.collection_categories.serializers import CollectionCategoryMiniSerializer
from api.institutes import serializers as InstituteSerializers
from api.students.models import Student
from api.students.serializers import StudentReadMiniSerializer


class FeeExemptedStudentReadSerializer(serializers.ModelSerializer):
    student = StudentReadMiniSerializer(read_only=True)
    category = CollectionCategoryMiniSerializer(read_only=True)

    class Meta:
        model = FeeExemptedStudent
        fields = "__all__"

class FeeExemptedStudentWriteSerializer(serializers.ModelSerializer):

    # Required to create; on an update the row already knows its student, so
    # an edit that only changes the reason or the term must not have to
    # repeat it (and a PATCH without it used to 500).
    student_school_id = serializers.CharField(
        source="student", write_only=True, required=False
    )
    student = serializers.CharField(read_only=True)
    #: Inactive rows are kept for the record and ignored by generation runs.
    status = serializers.BooleanField(required=False)

    class Meta:
        model = FeeExemptedStudent
        fields = [
            "student_school_id",
            "category",
            "status",
            "reason",
            "academic_year",
            "semester",
            "student",
        ]

    def validate_student_school_id(self, value):
        try:
            student = Student.objects.get(
                s_studentID=value,
                program__institute=self.context["request"].user.institute,
            )
            return student
        except Student.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid student School ID or student is outside assigned institute."
            )

    def validate(self, attrs):
        instance = self.instance
        student = attrs.get("student") or (instance.student if instance else None)
        if student is None:
            raise serializers.ValidationError(
                {"student_school_id": "This field is required."}
            )
        category = attrs.get("category") or (instance.category if instance else None)
        academic_year = attrs.get("academic_year") or (
            instance.academic_year if instance else None
        )
        semester = attrs.get("semester") or (instance.semester if instance else None)
        user = self.context["request"].user

        if student.program.institute != user.institute:
            raise serializers.ValidationError(
                "Can't exempt students outside assigned institute."
            )

        duplicates = FeeExemptedStudent.objects.filter(
            student=student,
            category=category,
            academic_year=academic_year,
            semester=semester,
        )
        if instance is not None:
            # Re-saving a row with its own values is not a duplicate of itself.
            duplicates = duplicates.exclude(pk=instance.pk)
        if duplicates.exists():
            raise serializers.ValidationError(
                f"This student is already exempted for this category for this {academic_year} - {semester} Semester."
            )

        return attrs


class FeeExemptedStudentBulkWriteSerializer(serializers.Serializer):
    """
    Bulk variant of FeeExemptedStudentWriteSerializer: one category / reason /
    term applied to a whole list of students in a single request.

    Unknown or out-of-institute school IDs fail the request as a whole, so a
    typo can't silently exempt only half the list. Students already exempted
    for that category and term are skipped and reported, which makes re-posting
    the same list safe.
    """

    student_school_ids = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
        max_length=500,
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=CollectionCategory.objects.all()
    )
    reason = serializers.CharField(max_length=100)
    # Pulled from the model fields so both write paths accept exactly the
    # same values.
    academic_year = serializers.ChoiceField(
        choices=FeeExemptedStudent._meta.get_field("academic_year").choices
    )
    semester = serializers.ChoiceField(
        choices=FeeExemptedStudent._meta.get_field("semester").choices
    )

    def validate(self, attrs):
        user = self.context["request"].user
        institute_id = getattr(user, "institute_id", None)
        if not institute_id:
            raise serializers.ValidationError(
                "Your account is not attached to an institute."
            )

        if attrs["category"].institute_id != institute_id:
            raise serializers.ValidationError(
                {"category": "Category does not exist or is outside your institute scope."}
            )

        # Dedupe while keeping the caller's order for the response lists.
        requested_ids = list(dict.fromkeys(attrs["student_school_ids"]))

        students_by_school_id = {
            student.s_studentID: student
            for student in Student.objects.filter(
                s_studentID__in=requested_ids,
                program__institute_id=institute_id,
            )
        }

        missing = [sid for sid in requested_ids if sid not in students_by_school_id]
        if missing:
            raise serializers.ValidationError(
                {
                    "student_school_ids": (
                        "Invalid student School IDs or outside assigned "
                        f"institute: {', '.join(missing)}"
                    )
                }
            )

        attrs["students"] = [students_by_school_id[sid] for sid in requested_ids]
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        category = validated_data["category"]
        academic_year = validated_data["academic_year"]
        semester = validated_data["semester"]
        students = validated_data["students"]

        already_exempted_pks = set(
            FeeExemptedStudent.objects.filter(
                student__in=students,
                category=category,
                academic_year=academic_year,
                semester=semester,
            ).values_list("student_id", flat=True)
        )

        to_create = [s for s in students if s.pk not in already_exempted_pks]

        # bulk_create is safe here: FeeExemptedStudent has no custom save()
        # and no signal receivers that a bulk write would bypass.
        FeeExemptedStudent.objects.bulk_create(
            FeeExemptedStudent(
                student=student,
                category=category,
                reason=validated_data["reason"],
                academic_year=academic_year,
                semester=semester,
                created_by=user,
                updated_by=user,
            )
            for student in to_create
        )

        return {
            "created": [s.s_studentID for s in to_create],
            "skipped_existing": [
                s.s_studentID for s in students if s.pk in already_exempted_pks
            ],
        }


class FeeExemptedStudentBulkResultSerializer(serializers.Serializer):
    """Response shape for the bulk exemption endpoint."""

    created_count = serializers.IntegerField()
    skipped_count = serializers.IntegerField()
    created = serializers.ListField(child=serializers.CharField())
    skipped_existing = serializers.ListField(child=serializers.CharField())


class FeeExemptedStudentBulkDeleteSerializer(serializers.Serializer):
    """
    Bulk removal of exemptions by their record ids (the ids the list
    endpoint returns), so a screen can delete exactly the rows the user
    ticked.

    Ids that do not exist - or belong to another institute, which looks
    identical from the caller's side - are skipped and reported rather than
    failing the request, which makes re-posting the same selection safe:
    the second call simply finds nothing left to delete.
    """

    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=500,
        help_text="FeeExemptedStudent record ids to delete.",
    )

    def validate_ids(self, value):
        # Dedupe, keeping the caller's order for the response lists.
        return list(dict.fromkeys(value))


class FeeExemptedStudentBulkDeleteResultSerializer(serializers.Serializer):
    """Response shape for the bulk delete endpoint."""

    deleted_count = serializers.IntegerField()
    skipped_count = serializers.IntegerField()
    deleted = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="Ids that existed in your scope and were removed.",
    )
    skipped_missing = serializers.ListField(
        child=serializers.IntegerField(),
        help_text="Ids that matched nothing (already deleted, or outside your institute).",
    )

