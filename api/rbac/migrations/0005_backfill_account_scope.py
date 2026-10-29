"""
Put existing accounts where their roles say they are.

Two things decided what an account could see and both had been left unset
on real accounts:

  * `User.school`. Every scoped list for a School Admin is "your school",
    read from `user.school` or, failing that, the institute's school. An
    account attached to an institute but with `school` empty is fine for
    that; one attached to neither sees nothing. This fills `school` from
    the institute wherever it can, so the two agree from now on. An account
    attached to nothing at all stays that way - there is nothing to infer
    it from - and the console's *Set scope* action is how it gets placed.

  * `User.is_staff`. Older code paths still branch on it, and an account
    put into a staff role through the Django admin or an old invitation had
    it off, so every scoped list came back empty for a perfectly good School
    Admin. `sync_staff_flags` sets it for every member of a role that
    declares `staff_flag`; it never demotes.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    User = apps.get_model("users", "User")

    for user in User.objects.filter(school__isnull=True, institute__isnull=False).select_related("institute"):
        user.school_id = user.institute.school_id
        user.save(update_fields=["school"])

    # The real service, on the real model: it reads the role catalog.
    from api.rbac.services import sync_staff_flags

    sync_staff_flags(demote=False)


class Migration(migrations.Migration):

    dependencies = [
        ("rbac", "0004_payment_settings"),
        ("users", "0013_alter_user_options_userregistrationtoken_school_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
