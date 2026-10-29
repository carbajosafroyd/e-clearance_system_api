"""
Re-sync the role catalog after two grants moved.

    users.change_user_email     joined USER_ADMINISTRATION - an administrator
                                may point an account in their scope at a new
                                email address (a confirmation link goes to
                                that address; nothing changes until it is
                                opened).
    rbac.manage_roles           joined INSTITUTE_ADMINISTRATION - an institute
    users.assign_user_role      administrator may change the roles of their
                                own institute's accounts, bounded by the
                                `DELEGATION` table in `api/rbac/roles.py`.

The first is a new permission (declared in `users/0013`), so it needs the
same `create_permissions` pass `rbac/0002` does before it can be granted.
Every role is re-synced rather than the three that changed, so a deployment
that applies this migration without running `seed_rbac` afterwards is in
the state `seed_rbac` would leave it in.
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
        ("rbac", "0002_institute_roles"),
        ("users", "0013_alter_user_options_userregistrationtoken_school_and_more"),
    ]

    operations = [
        # A permission sync has no meaningful reverse: rolling back leaves
        # the groups as they are, and the next `seed_rbac` settles them.
        migrations.RunPython(sync_all_roles, migrations.RunPython.noop),
    ]
