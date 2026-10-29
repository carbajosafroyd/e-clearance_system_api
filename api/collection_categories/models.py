from django.db import models
from core.BaseModel import ActorStampedModel, FullyAuditedModel
from django.conf import settings
from api.students.models import Student
from django.db import models
from api.institutes.models import Institute
from core.choices import AcademicYear, get_default_academic_year, Semester


class CollectionCategory(ActorStampedModel):
    institute = models.ForeignKey(
        "institutes.Institute",
        on_delete=models.CASCADE,
        related_name="collection_categories",
    )
    category_name = models.CharField(max_length=150, db_index=True)
    collection_fee = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "collection_categories"
        permissions = [
            ("bulk_add_collectioncategory", "Can create collection categories in bulk"),
        ]
        ordering = ["category_name"]
        unique_together = ("category_name", "institute")

    def __str__(self):
        return f"{self.category_name}"


class FeeExemptedStudent(FullyAuditedModel):
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="fee_excempted"
    )
    category = models.ForeignKey(
        CollectionCategory, on_delete=models.CASCADE, related_name="fee_excempted"
    )
    status = models.BooleanField(default=True, max_length=15)
    reason = models.CharField(max_length=100, null=False)
    academic_year = models.CharField(choices=AcademicYear.choices(), null=False, max_length=9)
    semester = models.CharField(choices=Semester.choices, null=False, max_length=3)

    class Meta:
        db_table = "fee_exempted_students"
        # The bulk delete rides on POST (a DELETE body does not survive every
        # proxy), so it cannot be inferred from the HTTP verb.
        permissions = [
            ("bulk_add_feeexemptedstudent", "Can exempt students in bulk"),
            ("bulk_delete_feeexemptedstudent", "Can remove exemptions in bulk"),
        ]


class InstituteGcashAccount(FullyAuditedModel):
    class GcashAccountStatus(models.TextChoices):
        FULL = "full", "Account limit reached"
        AVAILABLE = "available", "Account available"
        DISABLED = "disabled", "Account Disabled"

    institute = models.ForeignKey(
        Institute, on_delete=models.CASCADE, related_name="gcash_accounts"
    )
    gcash_qr = models.CharField(max_length=150)
    #: The Cloudinary account version the QR image was uploaded under - see
    #: CLOUDINARY_ACCOUNTS in settings. Null means "not known yet": the
    #: helpers look it up across accounts on first use.
    cloudinary_version = models.PositiveSmallIntegerField(null=True, blank=True)
    number = models.CharField(max_length=11, unique=True)
    hint = models.CharField(max_length=20)
    status = models.CharField(
        choices=GcashAccountStatus.choices,
        default=GcashAccountStatus.AVAILABLE,
        max_length=20,
    )
    # in consideration mag add og balance :> 

    class Meta:
        db_table = "institute_gcash_accounts"
