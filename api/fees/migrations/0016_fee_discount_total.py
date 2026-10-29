"""Give relief its own column on the fee.

Discounts used to be subtracted from `total_amount`, so the row could no
longer say what it had originally billed. This adds `discount_total`, moves
the historical relief back out of `total_amount` into it, and swaps the
balance ceiling to net-of-relief.

`balance` is never touched, forwards or back. Every fee's outstanding figure
is already right; only the split between "billed" and "forgiven" is being
reconstructed.

Operation order matters. The old ceiling comes off first and the new ones go
on last, because in between there are rows where neither holds: after the
column is added but before the backfill, `discount_total` is 0 while
`total_amount` is still net, and mid-backfill the two disagree by design.
"""

from decimal import Decimal

import django.db.models.expressions
from django.conf import settings
from django.db import migrations, models
from django.db.models import Sum


def restore_gross(apps, schema_editor):
    """Put approved discounts back onto `total_amount` and into the column."""
    Fee = apps.get_model("fees", "Fee")

    try:
        FeeDiscount = apps.get_model("contributions", "FeeDiscount")
    except LookupError:
        # A deployment that never installed contributions has no relief to
        # reconstruct - every fee is already gross.
        return

    applied = (
        FeeDiscount.objects.filter(status="approved")
        .values("fee_id")
        .annotate(total=Sum("amount"))
    )

    for row in applied.iterator(chunk_size=500):
        if not row["total"]:
            continue
        Fee.objects.filter(pk=row["fee_id"]).update(
            total_amount=models.F("total_amount") + row["total"],
            discount_total=row["total"],
        )


def collapse_into_total(apps, schema_editor):
    """
    Fold relief back into `total_amount`, returning the table to the shape
    the old code expects.

    Driven off the column rather than off the discount rows: by the time
    anybody rolls this back there may be reductions from somewhere other than
    contributions, and the column is the one thing that knows about all of
    them.
    """
    Fee = apps.get_model("fees", "Fee")
    Fee.objects.filter(discount_total__gt=0).update(
        total_amount=models.F("total_amount") - models.F("discount_total")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("collection_categories", "0012_alter_collectioncategory_options_and_more"),
        ("fees", "0015_alter_fee_options_alter_generatedfeebatch_options"),
        ("institutes_attendance_events", "0004_remove_instituteattendanceevent_students"),
        ("students", "0014_alter_student_options"),
        # The backfill reads FeeDiscount rows.
        ("contributions", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="fee",
            name="fee_balance_within_total",
        ),
        migrations.AddField(
            model_name="fee",
            name="discount_total",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0.00"), max_digits=12
            ),
        ),
        migrations.RunPython(restore_gross, collapse_into_total),
        migrations.AddConstraint(
            model_name="fee",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "balance__lte",
                        django.db.models.expressions.CombinedExpression(
                            models.F("total_amount"), "-", models.F("discount_total")
                        ),
                    )
                ),
                name="fee_balance_within_net",
            ),
        ),
        migrations.AddConstraint(
            model_name="fee",
            constraint=models.CheckConstraint(
                condition=models.Q(("discount_total__gte", 0)),
                name="fee_discount_total_not_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="fee",
            constraint=models.CheckConstraint(
                condition=models.Q(("discount_total__lte", models.F("total_amount"))),
                name="fee_discount_within_total",
            ),
        ),
    ]
