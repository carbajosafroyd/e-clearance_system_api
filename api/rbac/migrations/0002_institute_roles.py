"""
Create the two institute-level roles that belong to no single system.

    Institute Admins    runs one institute: students, programs, GCash
                        accounts, settings, people. Appoints its staff.
    Institute Staffs    the institute's day-to-day records. Appoints nobody.

## Why a migration and not just `seed_rbac`

`seed_rbac` is the normal way roles reach the database, and it still owns
them: it will re-sync these two on every run like the others. But a role
that exists only after somebody remembers to run a command is a role that
is missing on the day it is needed - the console lists it in the role
picker greyed out with "not in the database yet", and the first invitation
into it fails. Creating the groups here means every environment that has
run `migrate` has them.

The permission set is read from `api/rbac/roles.py` at migration time, so
it matches what `seed_rbac` would grant. Permissions are created by Django
AFTER all migrations (the `post_migrate` signal), so on a fresh database
the tables this reads would be empty; `create_permissions` is called for
every app first to make sure they exist. On an existing database that is a
no-op.

`School Admins` inherits from `Institute Admins` from now on, so it is
re-synced here too - otherwise a school administrator would lack the new
role's permissions until the next `seed_rbac`.
"""
from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations

ROLE_NAMES = ("Institute Staffs", "Institute Admins", "School Admins")


def create_roles(apps, schema_editor):
    # The real registry, not the migration's frozen one: `create_permissions`
    # needs app configs with models, and `sync_role` reads the live catalog.
    for app_config in global_apps.get_app_configs():
        app_config.models_module = getattr(app_config, "models_module", None) or True
        create_permissions(app_config, verbosity=0)

    from api.rbac.roles import ROLE_DEFINITIONS
    from api.rbac.services import sync_role

    for name in ROLE_NAMES:
        if name in ROLE_DEFINITIONS:
            sync_role(name)


def remove_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    # Only the two new groups; School Admins predates this migration.
    Group.objects.filter(name__in=["Institute Staffs", "Institute Admins"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
        # Every app whose permissions the two roles name, at the migration
        # that last added one - so the permission rows can exist by now.
        ("users", "0012_alter_user_options"),
        ("students", "0014_alter_student_options"),
        ("programs", "0005_alter_program_created_at_alter_program_updated_at_and_more"),
        ("institutes", "0007_institute_acronym"),
        ("schools", "0010_alter_school_created_at_alter_school_updated_at_and_more"),
        ("collection_categories", "0012_alter_collectioncategory_options_and_more"),
        ("system_settings", "0005_systemsetting_system"),
        ("notifications", "0007_alter_notification_source_alter_notification_type"),
        ("audits", "0003_alter_auditlog_options_alter_auditlog_system"),
    ]

    operations = [
        migrations.RunPython(create_roles, remove_roles),
    ]
