from django.db import models
from core.BaseModel import TimeStampedModel
from core.choices import Semester
from core.email_sender_model import EmailSenderMixin


class School(EmailSenderMixin, TimeStampedModel):
    # EmailSenderMixin: email_host_user / email_host_password_encrypted /
    # default_from_email - the Gmail account the school's mail goes out
    # through. See core/services/email_sender.py for who uses it.
    school_name = models.CharField(max_length=100, unique=True)
    logo = models.TextField(max_length=1200, null=True)
    #: The wide picture behind the sign-in page for school-wide accounts.
    background_image = models.TextField(max_length=200, null=True, blank=True)
    #: The Cloudinary account version the logo was uploaded under - see
    #: CLOUDINARY_ACCOUNTS in settings. Null means "not known yet": the
    #: helpers look it up across accounts on first use.
    logo_cloudinary_version = models.PositiveSmallIntegerField(null=True, blank=True)
    #: The Cloudinary account version the background image was uploaded under - see
    #: CLOUDINARY_ACCOUNTS in settings. Null means "not known yet": the
    #: helpers look it up across accounts on first use.
    background_cloudinary_version = models.PositiveSmallIntegerField(null=True, blank=True)
    short_name = models.CharField(max_length=10, null=True)
    location = models.TextField(max_length=1200)

    class Meta:
        db_table = "schools"

    def __str__(self):
        return self.school_name


class SchoolBuilding(TimeStampedModel):

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="school_buildings"
    )
    name = models.CharField(max_length=50)
    max_floor = models.PositiveIntegerField(null=False, blank=False)

    class Meta:
        db_table = "school_buildings"
        indexes = [
            models.Index(fields=["school", "name"]),
        ]

        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"], name="unique_building_per_school"
            )
        ]

    def __str__(self):
        return self.name


# class AppSetting(models.Model):  # dri ra i butang gi kapoy kog himo bag o WHAHAH
#     key = models.CharField(max_length=100, unique=True)
#     value = models.JSONField()

#     class Meta:
#         db_table = "app_settings"

#     def __str__(self):
#         return self.key
