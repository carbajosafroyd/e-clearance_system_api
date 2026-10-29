from django.db import models
from core.BaseModel import TimeStampedModel
from core.email_sender_model import EmailSenderMixin


class Institute(EmailSenderMixin, TimeStampedModel):
    # EmailSenderMixin: email_host_user / email_host_password_encrypted /
    # default_from_email - the institute's own Gmail sender. Falls back to
    # the school's, then the platform's; see core/services/email_sender.py.
    institute_name = models.CharField(max_length=100, unique=True, db_index=True)
    #: Short form - "IC", "ITE". Programs already carry one, and matching on
    #: a display name to decide policy is the sort of thing that breaks the
    #: day somebody fixes a typo in it.
    #:
    #: `db_default` as well as `default`: a Django default is applied in
    #: Python, so any insert that does not go through this model class - a
    #: historical model in a migration test, a data migration, raw SQL - hits
    #: the NOT NULL and fails.
    acronym = models.CharField(
        max_length=12, blank=True, default="", db_default="", db_index=True
    )
    logo = models.TextField(max_length=100, null=True)
    #: The wide picture behind the institute's sign-in page. A Cloudinary
    #: public id like `logo`; null means "use the app's default".
    background_image = models.TextField(max_length=200, null=True, blank=True)
    #: The Cloudinary account version the logo was uploaded under - see
    #: CLOUDINARY_ACCOUNTS in settings. Null means "not known yet": the
    #: helpers look it up across accounts on first use.
    logo_cloudinary_version = models.PositiveSmallIntegerField(null=True, blank=True)
    #: The Cloudinary account version the background image was uploaded under - see
    #: CLOUDINARY_ACCOUNTS in settings. Null means "not known yet": the
    #: helpers look it up across accounts on first use.
    background_cloudinary_version = models.PositiveSmallIntegerField(null=True, blank=True)
    school = models.ForeignKey(
        "schools.School", on_delete=models.CASCADE, related_name="institutes"
    )

    class Meta:
        db_table = "institutes"

    def __str__(self):
        return self.institute_name
