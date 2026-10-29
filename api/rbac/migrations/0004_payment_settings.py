"""
Re-sync the role catalog after `system_settings.change_payment_settings`
joined INSTITUTE_ADMINISTRATION and the collection-system administrator's
settings grants.

The permission itself is declared in `system_settings/0006`; this makes
sure the rows exist and then puts the grant on the roles, so a deployment
that runs `migrate` without `seed_rbac` still lets its institute
administrators close and reopen the online-payment lane.
"""
from django.apps import apps as global_apps
from django.contrib.auth.management import create_permissions
from django.db import migrations


def sync_all_roles(apps, schema_editor):
    for app_config in global_apps.get_app_configs():
        app_config.models_module = getattr(app_config, "models_module", None) or True
        create_permissions(app_config, verbosity=0)

    from api.rbac.roles import ROLE_DEFINITIONS
    from api.rbac.services import sync_role

    for name in ROLE_DEFINITIONS:
        sync_role(name)


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0003_delegation_grants"),
        ("system_settings", "0006_alter_systemsetting_options"),
    ]

    operations = [
        migrations.RunPython(sync_all_roles, migrations.RunPython.noop),
    ]
