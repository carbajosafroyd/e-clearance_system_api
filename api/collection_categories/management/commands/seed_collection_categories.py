"""Seed collection categories, GCash accounts and a few fee exemptions."""

from __future__ import annotations

from decimal import Decimal

from api.collection_categories.models import (
    CollectionCategory,
    FeeExemptedStudent,
    InstituteGcashAccount,
)
from api.fees.constants import ATTENDANCE_CATEGORY_NAME
from api.institutes.models import Institute
from api.seeding.support import SeedCommand
from api.students.models import Student
from core.choices import Semester, get_default_academic_year

CATEGORIES = [
    ("Tuition Fee", "1500.00", "Base tuition for the term."),
    ("Laboratory Fee", "750.00", "Computer and science laboratory use."),
    ("Organization Fee", "150.00", "Student organisation dues."),
    ("Locker Rental", "200.00", "One locker for the term."),
    (ATTENDANCE_CATEGORY_NAME, "50.00", "Charged per missed attendance slot."),
]

EXEMPTION_REASONS = [
    "Academic scholar",
    "Working student",
    "Athletic scholar",
    "Financial assistance",
]


class Command(SeedCommand):
    seed_name = "collection categories"
    help = "Create demo collection categories, GCash accounts and exemptions."
    depends_on = ("seed_institutes", "seed_students")

    def reset(self, **options):
        FeeExemptedStudent.objects.all().delete()
        InstituteGcashAccount.objects.all().delete()
        deleted, _ = CollectionCategory.objects.all().delete()
        self.warn(f"deleted {deleted} collection category row(s)")

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")

        created_categories = 0
        created_accounts = 0

        for index, institute in enumerate(institutes):
            for name, amount, description in CATEGORIES:
                _, made = CollectionCategory.objects.get_or_create(
                    institute=institute,
                    category_name=name,
                    defaults={
                        "collection_fee": Decimal(amount),
                        "description": description,
                    },
                )
                created_categories += int(made)

            # The number is unique across institutes, so it is derived from
            # the institute's position rather than a fixed literal.
            number = f"09{170000000 + index:09d}"[:11]
            _, made = InstituteGcashAccount.objects.get_or_create(
                number=number,
                defaults={
                    "institute": institute,
                    "gcash_qr": f"seed/gcash-qr-{institute.pk}",
                    "hint": f"***{number[-4:]}",
                    "status": InstituteGcashAccount.GcashAccountStatus.AVAILABLE,
                },
            )
            created_accounts += int(made)

        self.created("collection category", created_categories)
        self.created("GCash account", created_accounts)
        self._seed_exemptions()

    def _seed_exemptions(self):
        academic_year = get_default_academic_year()
        created = 0

        for institute in Institute.objects.all():
            categories = list(
                CollectionCategory.objects.filter(institute=institute).exclude(
                    category_name=ATTENDANCE_CATEGORY_NAME
                )
            )
            students = list(
                Student.objects.filter(program__institute=institute).order_by("id")
            )
            if not categories or not students:
                continue

            # Roughly one student in eight is exempt from one category.
            for student in self.sample(students, max(1, len(students) // 8)):
                _, made = FeeExemptedStudent.objects.get_or_create(
                    student=student,
                    category=self.pick(categories),
                    academic_year=academic_year,
                    semester=Semester.FIRST,
                    defaults={"reason": self.pick(EXEMPTION_REASONS), "status": True},
                )
                created += int(made)

        self.created("fee exemption", created)
