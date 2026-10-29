from django.db import models
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError

from core.BaseModel import ActorStampedModel
from api.students.models import Student
from api.users.models import User
from api.institutes.models import Institute


def validate_year_levels(value):
    if not isinstance(value, list):
        raise ValidationError("target_year_levels must be a list.")
    for item in value:
        if not isinstance(item, int):
            raise ValidationError("Each target year level must be an integer.")


class ClearanceType(ActorStampedModel):
    institute = models.ForeignKey(
    Institute,
    on_delete=models.PROTECT,
    related_name="clearance_types",
    null=True,
    blank=True,
)
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    academic_year = models.CharField(max_length=20)
    semester = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)
    target_year_levels = models.JSONField(default=list, validators=[validate_year_levels])
    is_issued = models.BooleanField(default=False)
    issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "clearance_types"
        ordering = ["-created_at"]
        permissions = [
            ("issue_clearancetype", "Can issue a clearance to every eligible student"),
        ]
        indexes = [
            models.Index(fields=["institute", "academic_year", "semester"]),
            models.Index(fields=["institute", "is_active"]),
            models.Index(fields=["institute", "is_issued"]),
        ]
        constraints = [
    models.UniqueConstraint(
        fields=["institute", "name", "academic_year", "semester"],
        name="unique_clearance_type_per_institute_term",
    )
]

    def __str__(self):
        return f"{self.name} ({self.academic_year} - {self.semester})"


class ClearanceRequirement(ActorStampedModel):
    clearance_type = models.ForeignKey(
        ClearanceType,
        on_delete=models.CASCADE,
        related_name="requirements",
    )
    name = models.CharField(max_length=100)
    authorizing_role = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="clearance_requirements",
        help_text="Primary/legacy authorizing role. Use authorizing_roles for multiple allowed signers.",
    )
    authorizing_roles = models.ManyToManyField(
        Group,
        blank=True,
        related_name="multi_authorized_clearance_requirements",
        help_text="All roles allowed to clear this requirement.",
    )
    order_sequence = models.PositiveIntegerField()

    class Meta:
        db_table = "clearance_requirements"
        ordering = ["order_sequence", "id"]
        indexes = [
            models.Index(fields=["clearance_type"]),
            models.Index(fields=["authorizing_role"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["clearance_type", "name"],
                name="unique_requirement_name_per_clearance_type",
            ),
            models.UniqueConstraint(
                fields=["clearance_type", "order_sequence"],
                name="unique_requirement_order_per_clearance_type",
            ),
        ]

    def __str__(self):
        return f"{self.clearance_type.name} - {self.name}"


class StudentClearance(ActorStampedModel):
    class ClearanceStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="student_clearances",
    )
    clearance_type = models.ForeignKey(
        ClearanceType,
        on_delete=models.CASCADE,
        related_name="student_clearances",
    )
    status = models.CharField(
        max_length=20,
        choices=ClearanceStatus.choices,
        default=ClearanceStatus.PENDING,
    )
    date_initiated = models.DateTimeField(auto_now_add=True)
    date_completed = models.DateTimeField(null=True, blank=True)
    is_enrolled = models.BooleanField(default=False)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    enrolled_by_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="enrolled_clearances",
    )

    class Meta:
        db_table = "student_clearances"
        ordering = ["-date_initiated"]
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["clearance_type", "status"]),
            models.Index(fields=["clearance_type", "is_enrolled"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "clearance_type"],
                name="unique_student_clearance_per_type",
            )
        ]

    def __str__(self):
        return f"{self.student} - {self.clearance_type.name}"


class StudentClearanceItem(ActorStampedModel):
    class ItemStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        CLEARED = "cleared", "Cleared"
        HAS_ISSUE = "has_issue", "Has Issue"

    student_clearance = models.ForeignKey(
        StudentClearance,
        on_delete=models.CASCADE,
        related_name="items",
    )
    requirement = models.ForeignKey(
        ClearanceRequirement,
        on_delete=models.CASCADE,
        related_name="student_clearance_items",
    )
    status = models.CharField(
        max_length=20,
        choices=ItemStatus.choices,
        default=ItemStatus.PENDING,
    )
    amount_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    remarks = models.TextField(null=True, blank=True)
    cleared_by_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cleared_clearance_items",
    )
    cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "student_clearance_items"
        ordering = ["requirement__order_sequence", "id"]
        permissions = [
            ("sign_studentclearanceitem", "Can sign off a clearance requirement"),
        ]
        indexes = [
            models.Index(fields=["student_clearance", "status"]),
            models.Index(fields=["requirement", "status"]),
            models.Index(fields=["cleared_by_user"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["student_clearance", "requirement"],
                name="unique_requirement_per_student_clearance",
            )
        ]

    def __str__(self):
        return f"{self.student_clearance} - {self.requirement.name}"