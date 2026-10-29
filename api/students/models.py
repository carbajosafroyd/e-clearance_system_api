from django.db import models
from core.BaseModel import ActorStampedModel

from config import settings
from django.core.validators import MinLengthValidator


class Student(ActorStampedModel):
    class StudentStatus(models.TextChoices):
        ENROLLED = "enrolled", "ENROLLED"
        GRADUATED = "graduated", "GRADUATED"
        DROPPED = "dropped", "DROPPED"
        TO_BE_UPDATED = "to_be_updated", "TO BE UPDATED"

    s_studentID = models.CharField(
        max_length=10,
        unique=True,
        validators=[MinLengthValidator(10)],
    )
    program = models.ForeignKey(
        "programs.Program", on_delete=models.CASCADE, related_name="students"
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student",
    )
    s_rfid = models.CharField(
        max_length=10,
        unique=True,
        null=True,
        validators=[MinLengthValidator(10)],
    )
    s_fname = models.CharField(max_length=100)
    s_mname = models.CharField(max_length=100, null=True, blank=True)
    s_lname = models.CharField(max_length=100)
    s_suffix = models.CharField(max_length=20, null=True, blank=True)
    # s_email = models.EmailField(unique=True) # to be remove :> REMOVED FOR INTEGRITY
    s_set = models.CharField(max_length=50, null=True)
    s_lvl = models.PositiveIntegerField()
    s_status = models.CharField(
        max_length=15, choices=StudentStatus.choices, default=StudentStatus.ENROLLED
    )
    # s_image = models.ImageField(upload_to="students/", null=True)
    s_image = models.CharField(max_length=255, null=True, blank=True)
    #: The Cloudinary account version s_image was uploaded under - see
    #: CLOUDINARY_ACCOUNTS in settings. Null means "not known yet": the
    #: helpers look it up across accounts on first use.
    cloudinary_version = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta: # type: ignore
        db_table = "students"
        permissions = [
            ("import_students", "Can bulk import/update students from a file"),
            ("view_student_fee_summary", "Can view another student's fee summary"),
        ]
        ordering = ["s_lname"]
        indexes = [
            models.Index(fields=["s_status"]),
            models.Index(fields=["program", "s_status"]),
            models.Index(fields=["s_lname"]),
        ]

    def __str__(self):
        return f"{self.s_lname}, {self.s_fname} ({self.s_studentID})"
