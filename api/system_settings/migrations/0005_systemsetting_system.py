"""Give every setting the system it configures, and retire two dead ideas.

The `system` column is denormalised from the key. `core.setting_buckets` is
still the authority - it stamps new rows and this migration backfills the old
ones - but a frontend fetching its own settings has to filter in the database
rather than pull every row on the platform and sort them out in Python.

Two sets of keys go at the same time:

* `maintenance_mode` / `maintenance_message`, the institute-wide shutter. It
  was replaced by the per-system pair, and nothing read it: the API never
  enforced it and the collection app was changed to read
  `collection_*_maintenance_mode` instead. One institute had it set to
  `true` - a switch that had not closed anything since the day it was flipped.

* `student_portal_*`, which came from a hand-written system list that had
  drifted from `core.choices.SystemScope`. There is no student portal system;
  there is a student *audience*, which every system already has - so
  `student_portal_officer_maintenance_mode` was asking about officers in a
  place that has none.

Deliberately self-contained: no import from `core.setting_buckets`. A data
migration has to describe the database as it was on this date, and that module
is edited every time a setting is added.
"""

from django.db import migrations, models


#: `core.choices.SystemScope` as of this migration.
SYSTEM_PREFIXES = ("collection", "locker", "attendance", "clearance", "voting")
PLATFORM = "platform"

#: Replaced by the per-system shutters, or never a system at all.
RETIRED_KEYS = (
    "maintenance_mode",
    "maintenance_message",
    "student_portal_officer_maintenance_mode",
    "student_portal_officer_maintenance_message",
    "student_portal_student_maintenance_mode",
    "student_portal_student_maintenance_message",
)


def system_for(setting_key):
    """Which system a key configures, by its own name."""
    for system in SYSTEM_PREFIXES:
        if setting_key.startswith(f"{system}_"):
            return system
    # Every optional module - contributions, points, complaints, coupons - is
    # part of the collection system: filed against fees, paid out against fees.
    if setting_key.startswith("feature_"):
        return "collection"
    return PLATFORM


def stamp_and_retire(apps, schema_editor):
    SystemSetting = apps.get_model("system_settings", "SystemSetting")

    SystemSetting.objects.filter(setting_key__in=RETIRED_KEYS).delete()

    for system in SYSTEM_PREFIXES:
        SystemSetting.objects.filter(setting_key__startswith=f"{system}_").update(
            system=system
        )
    SystemSetting.objects.filter(setting_key__startswith="feature_").update(
        system="collection"
    )


def unstamp(apps, schema_editor):
    """The column goes; the retired rows are not resurrected.

    Recreating them would mean inventing values, and the only one that was
    ever non-default said an institute was closed when it was not.
    """
    SystemSetting = apps.get_model("system_settings", "SystemSetting")
    SystemSetting.objects.update(system=PLATFORM)


class Migration(migrations.Migration):

    dependencies = [
        ("system_settings", "0004_alter_systemsetting_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsetting",
            name="system",
            field=models.CharField(
                choices=[
                    ("collection", "Collection Management System"),
                    ("locker", "Locker Management System"),
                    ("attendance", "Attendance Management System"),
                    ("clearance", "Clearance Management System"),
                    ("voting", "Voting System"),
                    ("platform", "Platform"),
                ],
                db_index=True,
                default="platform",
                max_length=20,
            ),
        ),
        migrations.AlterModelOptions(
            name="systemsetting",
            options={
                "ordering": ["setting_key"],
                "permissions": [
                    ("bulk_change_systemsetting", "Can update several settings at once"),
                    (
                        "change_term_settings",
                        "Can change the current academic year and semester",
                    ),
                    (
                        "change_feature_settings",
                        "Can switch optional modules on and off",
                    ),
                    ("change_maintenance", "Can close a system to the staff who run it"),
                    (
                        "change_student_maintenance",
                        "Can close the student side while staff keep working",
                    ),
                    ("bypass_maintenance", "Can keep working while a system is closed"),
                    (
                        "manage_school_settings",
                        "Can manage settings for every institute in their school",
                    ),
                ],
            },
        ),
        migrations.RunPython(stamp_and_retire, unstamp),
    ]
