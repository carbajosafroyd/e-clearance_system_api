"""
The backfill that moved historical relief out of `total_amount`.

Migrations are usually left untested, and this one should not be: it rewrites
money on every discounted fee, and it is the only chance to reconstruct a
gross figure from rows that no longer carry one. If it is wrong, it is wrong
silently and permanently.

`migrator` walks the real migration graph, so these run the same SQL and the
same Python a deployment will.
"""

from decimal import Decimal

from django.db.migrations.executor import MigrationExecutor
from django.db import connection
from django.test import TransactionTestCase


# Contributions is named alongside fees in both states: the historical
# project state only contains apps reachable from the targets, and the
# backfill reads FeeDiscount, so a state without it cannot seed the case
# this file exists to test.
CONTRIBUTIONS = ("contributions", "0001_initial")
BEFORE = [("fees", "0015_alter_fee_options_alter_generatedfeebatch_options"), CONTRIBUTIONS]
AFTER = [("fees", "0016_fee_discount_total"), CONTRIBUTIONS]


class DiscountTotalBackfillTests(TransactionTestCase):
    """
    TransactionTestCase, not TestCase: migrating inside an atomic block that
    is rolled back at the end does not exercise the schema editor honestly,
    and the constraint swap is half of what is being tested.
    """

    available_apps = None

    def migrate_to(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        # Leave the database where the rest of the suite expects it.
        self.migrate_to(AFTER)

    def seed(self, apps):
        """A fee whose total was already written down by an old discount."""
        School = apps.get_model("schools", "School")
        Institute = apps.get_model("institutes", "Institute")
        Program = apps.get_model("programs", "Program")
        Student = apps.get_model("students", "Student")
        Category = apps.get_model("collection_categories", "CollectionCategory")
        Fee = apps.get_model("fees", "Fee")
        FeeDiscount = apps.get_model("contributions", "FeeDiscount")

        school = School.objects.create(school_name="S", location="L")
        institute = Institute.objects.create(institute_name="I", school=school)
        program = Program.objects.create(name="P", institute=institute)
        student = Student.objects.create(
            s_studentID="2026-1",
            s_fname="A",
            s_lname="B",
            s_set="A",
            s_lvl=1,
            program=program,
        )
        category = Category.objects.create(
            institute=institute, category_name="Dues", collection_fee=Decimal("500.00")
        )

        # 500 billed, 100 discounted the old way (both columns written down),
        # then 150 paid against the reduced bill.
        fee = Fee.objects.create(
            student=student,
            category=category,
            total_amount=Decimal("400.00"),
            balance=Decimal("250.00"),
            status="pending",
        )
        FeeDiscount.objects.create(
            fee=fee,
            student=student,
            system="collection",
            points_spent=100,
            amount=Decimal("100.00"),
            status="approved",
        )
        # A fee nobody ever discounted, to prove the backfill leaves it alone.
        untouched = Fee.objects.create(
            student=student,
            category=category,
            total_amount=Decimal("300.00"),
            balance=Decimal("300.00"),
            status="pending",
        )
        return fee.pk, untouched.pk

    def test_relief_moves_out_of_the_total_and_the_balance_never_moves(self):
        old_apps = self.migrate_to(BEFORE)
        discounted_pk, untouched_pk = self.seed(old_apps)

        new_apps = self.migrate_to(AFTER)
        Fee = new_apps.get_model("fees", "Fee")

        discounted = Fee.objects.get(pk=discounted_pk)
        self.assertEqual(discounted.total_amount, Decimal("500.00"))
        self.assertEqual(discounted.discount_total, Decimal("100.00"))
        # The one number that must not have changed.
        self.assertEqual(discounted.balance, Decimal("250.00"))
        # ...and collected is what it always was: 400 - 250 before, and
        # 500 - 100 - 250 after.
        self.assertEqual(
            discounted.total_amount - discounted.discount_total - discounted.balance,
            Decimal("150.00"),
        )

        untouched = Fee.objects.get(pk=untouched_pk)
        self.assertEqual(untouched.total_amount, Decimal("300.00"))
        self.assertEqual(untouched.discount_total, Decimal("0.00"))
        self.assertEqual(untouched.balance, Decimal("300.00"))

    def test_it_rolls_back_to_exactly_what_was_there_before(self):
        old_apps = self.migrate_to(BEFORE)
        discounted_pk, untouched_pk = self.seed(old_apps)

        self.migrate_to(AFTER)
        rolled_back = self.migrate_to(BEFORE)

        Fee = rolled_back.get_model("fees", "Fee")
        discounted = Fee.objects.get(pk=discounted_pk)
        self.assertEqual(discounted.total_amount, Decimal("400.00"))
        self.assertEqual(discounted.balance, Decimal("250.00"))

        untouched = Fee.objects.get(pk=untouched_pk)
        self.assertEqual(untouched.total_amount, Decimal("300.00"))
        self.assertEqual(untouched.balance, Decimal("300.00"))
