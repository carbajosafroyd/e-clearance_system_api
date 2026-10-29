from django.db import migrations, models
import django.db.models.deletion


DEFAULT_NEW_CLEARANCE_ROLES = [
    "Treasurer",
    "Assistant Treasurer",
]


def seed_requirement_authorizing_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    ClearanceRequirement = apps.get_model("clearance", "ClearanceRequirement")

    for name in DEFAULT_NEW_CLEARANCE_ROLES:
        Group.objects.get_or_create(name=name)

    for requirement in ClearanceRequirement.objects.all().iterator():
        if requirement.authorizing_role_id:
            requirement.authorizing_roles.add(requirement.authorizing_role_id)


def reverse_seed_requirement_authorizing_roles(apps, schema_editor):
    ClearanceRequirement = apps.get_model("clearance", "ClearanceRequirement")
    for requirement in ClearanceRequirement.objects.all().iterator():
        requirement.authorizing_roles.clear()


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("clearance", "0003_studentclearance_enrollment"),
    ]

    operations = [
        migrations.AddField(
            model_name="clearancerequirement",
            name="authorizing_roles",
            field=models.ManyToManyField(
                blank=True,
                help_text="All roles allowed to clear this requirement.",
                related_name="multi_authorized_clearance_requirements",
                to="auth.group",
            ),
        ),
        migrations.RunPython(seed_requirement_authorizing_roles, reverse_seed_requirement_authorizing_roles),
    ]
