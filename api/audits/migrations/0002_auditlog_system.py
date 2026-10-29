from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audits", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="system",
            # Every audit row written so far came from the Collection flows,
            # so backfilling existing rows with "collection" is accurate.
            field=models.CharField(
                choices=[
                    ("collection", "Collection"),
                    ("locker", "Locker"),
                    ("attendance", "Attendance"),
                    ("voting", "Voting"),
                ],
                default="collection",
                max_length=20,
            ),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["system", "created_at"], name="audits_system_created_idx"
            ),
        ),
    ]
