from django.db import models
from django.conf import settings

from api.institutes.models import Institute
from core.choices import SystemScope


class SystemSetting(models.Model):
    """
    Key-Value store for system settings, isolated per institute.
    """

    institute = models.ForeignKey(
        Institute, on_delete=models.CASCADE, related_name="system_settings"
    )
    setting_key = models.CharField(max_length=100)

    setting_value = models.TextField()

    #: Which system this row configures.
    #:
    #: Denormalised from the key on purpose. `core.setting_buckets.system_for`
    #: is the authority and the seeder stamps it, but a frontend fetching its
    #: own settings needs to filter in the database rather than pull every row
    #: on the platform and sort them out in Python.
    #:
    #: PLATFORM means "every system needs this one" - the academic year is not
    #: the collection app's setting, it is the institute's - so a request for
    #: one system's settings gets the platform rows too.
    system = models.CharField(
        max_length=20,
        choices=SystemScope.choices,
        default=SystemScope.PLATFORM,
        db_index=True,
    )

    description = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("institute", "setting_key")
        permissions = [
            ("bulk_change_systemsetting", "Can update several settings at once"),
            # The store holds decisions that belong to different people:
            # running a term is the institute administrator's job, closing
            # the institute is their school's. One permission over all of it
            # meant whoever could set the academic year could also shut the
            # app. `core.setting_buckets` maps a key to one of these.
            ("change_term_settings", "Can change the current academic year and semester"),
            ("change_feature_settings", "Can switch optional modules on and off"),
            # The GCash lane, on or off. The collection desk's own decision -
            # see `core.setting_buckets.PAYMENTS`.
            ("change_payment_settings", "Can switch online payments on and off"),
            ("change_maintenance", "Can close a system to the staff who run it"),
            (
                "change_student_maintenance",
                "Can close the student side while staff keep working",
            ),
            # Deliberately separate from `change_maintenance`. "May close it"
            # and "is exempt from it" are different sentences: whoever closed
            # a system has to be able to reach the settings page to reopen it,
            # but a deployment should be able to grant the exemption without
            # granting the authority, or the other way round.
            ("bypass_maintenance", "Can keep working while a system is closed"),
            # The tenant rule for settings is "your own institute". A school
            # administrator answers for the institutes under them, so this
            # widens the reach to their school - and stops there.
            (
                "manage_school_settings",
                "Can manage settings for every institute in their school",
            ),
        ]
        ordering = ["setting_key"]

    def __str__(self):
        return f"{self.institute} - {self.setting_key}: {self.setting_value}"
