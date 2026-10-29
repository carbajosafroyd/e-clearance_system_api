from django.db import transaction
from django.utils import timezone
from rest_framework.serializers import ValidationError

from api.students.models import Student
from api.clearance.models import (
    ClearanceType,
    StudentClearance,
    StudentClearanceItem,
)


def _model_field_names(model_cls):
    return {field.name for field in model_cls._meta.get_fields()}


def _actor_defaults(model_cls, actor):
    if not actor:
        return {}

    field_names = _model_field_names(model_cls)
    defaults = {}

    if "created_by" in field_names:
        defaults["created_by"] = actor
    elif "created_by_user" in field_names:
        defaults["created_by_user"] = actor

    if "updated_by" in field_names:
        defaults["updated_by"] = actor
    elif "updated_by_user" in field_names:
        defaults["updated_by_user"] = actor

    actor_id = getattr(actor, "id", None)

    if actor_id is not None:
        if (
            "created_by_id" in field_names
            and "created_by" not in defaults
            and "created_by_user" not in defaults
        ):
            defaults["created_by_id"] = actor_id

        if (
            "updated_by_id" in field_names
            and "updated_by" not in defaults
            and "updated_by_user" not in defaults
        ):
            defaults["updated_by_id"] = actor_id

    return defaults


def _apply_actor_fields(instance, actor):
    if not actor:
        return

    field_names = _model_field_names(instance.__class__)
    actor_id = getattr(actor, "id", None)

    if "created_by" in field_names:
        instance.created_by = actor
    elif "created_by_user" in field_names:
        instance.created_by_user = actor
    elif "created_by_id" in field_names and actor_id is not None:
        instance.created_by_id = actor_id

    if "updated_by" in field_names:
        instance.updated_by = actor
    elif "updated_by_user" in field_names:
        instance.updated_by_user = actor
    elif "updated_by_id" in field_names and actor_id is not None:
        instance.updated_by_id = actor_id


def _validate_clearance_type_for_issuance(clearance_type: ClearanceType):
    if not clearance_type.institute_id:
        raise ValidationError(
            {"detail": "Clearance type must belong to an institute before issuing."}
        )

    if not clearance_type.target_year_levels:
        raise ValidationError(
            {"detail": "Clearance type must have at least one target year level."}
        )

    requirements_exist = clearance_type.requirements.exists()
    if not requirements_exist:
        raise ValidationError(
            {"detail": "Add at least one requirement before issuing."}
        )


def issue_clearance_to_target_students(clearance_type: ClearanceType, actor=None):
    _validate_clearance_type_for_issuance(clearance_type)

    requirements = list(
        clearance_type.requirements.all().order_by("order_sequence", "id")
    )

    target_students = list(
        Student.objects.select_related("program")
        .filter(
            program__institute_id=clearance_type.institute_id,
            s_lvl__in=clearance_type.target_year_levels,
            s_status=Student.StudentStatus.ENROLLED,
        )
        .order_by("id")
    )

    target_student_ids = [student.id for student in target_students]

    existing_student_ids = set(
        StudentClearance.objects.filter(
            clearance_type=clearance_type,
            student_id__in=target_student_ids,
        ).values_list("student_id", flat=True)
    )

    students_to_create_for = [
        student for student in target_students if student.id not in existing_student_ids
    ]

    created_clearances = 0
    created_items = 0
    skipped_clearances = len(existing_student_ids)

    clearance_defaults = {
        "status": StudentClearance.ClearanceStatus.PENDING,
        **_actor_defaults(StudentClearance, actor),
    }

    with transaction.atomic():
        new_clearances = []

        for student in students_to_create_for:
            student_clearance = StudentClearance(
                student=student,
                clearance_type=clearance_type,
                **clearance_defaults,
            )
            _apply_actor_fields(student_clearance, actor)
            new_clearances.append(student_clearance)

        if new_clearances:
            StudentClearance.objects.bulk_create(new_clearances)
            created_clearances = len(new_clearances)

            created_student_ids = [student.id for student in students_to_create_for]

            created_clearance_map = {
                sc.student_id: sc
                for sc in StudentClearance.objects.filter(
                    clearance_type=clearance_type,
                    student_id__in=created_student_ids,
                )
            }

            new_items = []

            for student in students_to_create_for:
                student_clearance = created_clearance_map.get(student.id)
                if not student_clearance:
                    continue

                for requirement in requirements:
                    item = StudentClearanceItem(
                        student_clearance=student_clearance,
                        requirement=requirement,
                        status=StudentClearanceItem.ItemStatus.PENDING,
                    )
                    _apply_actor_fields(item, actor)
                    new_items.append(item)

            if new_items:
                StudentClearanceItem.objects.bulk_create(new_items)
                created_items = len(new_items)

        clearance_type.is_issued = True
        clearance_type.issued_at = timezone.now()
        _apply_actor_fields(clearance_type, actor)
        clearance_type.save()

    return {
        "created_clearances": created_clearances,
        "created_items": created_items,
        "skipped_clearances": skipped_clearances,
        "target_students_count": len(target_students),
    }