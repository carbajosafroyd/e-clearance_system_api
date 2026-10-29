"""Rename `times_redeemed` to `uses_taken`, because it now counts holds too.

Claiming a coupon takes one of its uses. It has to: otherwise two students
can both claim the last use of a shared code, and whoever applies second is
left holding a coupon their account told them was theirs.

Once a claim counts, "times redeemed" is no longer what the column means —
it means how many uses are spoken for, spent or merely held. A rename rather
than a new field, so there is one number and it cannot disagree with itself.

The constraint is dropped and re-added because its expression names the
column.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("coupons", "0001_initial"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="coupon",
            name="coupon_not_over_redeemed",
        ),
        migrations.RenameField(
            model_name="coupon",
            old_name="times_redeemed",
            new_name="uses_taken",
        ),
        migrations.AddConstraint(
            model_name="coupon",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("uses_taken__lte", models.F("max_redemptions"))
                ),
                name="coupon_not_over_redeemed",
            ),
        ),
    ]
