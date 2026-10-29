# Generated for clearance enrollment tracking.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clearance", "0002_remove_clearancetype_unique_clearance_type_per_term_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="studentclearance",
            name="is_enrolled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="studentclearance",
            name="enrolled_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="studentclearance",
            name="enrolled_by_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="enrolled_clearances",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddIndex(
            model_name="studentclearance",
            index=models.Index(
                fields=["clearance_type", "is_enrolled"],
                name="student_cle_clearan_2f6b3d_idx",
            ),
        ),
    ]
