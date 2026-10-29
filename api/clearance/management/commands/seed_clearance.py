"""
Seed clearance templates, their requirements, and student clearances.

Rewritten to resolve its actors and signatory groups by *role* rather than
by hard-coded primary keys. The previous version demanded user_id=40..47 with
exact usernames, which only ever matched one developer's database and failed
outright on a fresh one.
"""

from __future__ import annotations

from django.contrib.auth.models import Group
from django.utils import timezone

from api.clearance.models import (
    ClearanceRequirement,
    ClearanceType,
    StudentClearance,
    StudentClearanceItem,
)
from api.institutes.models import Institute
from api.rbac.roles import Roles
from api.rbac.services import sync_role
from api.seeding.support import SeedCommand
from api.students.models import Student
from api.users.models import User
from core.choices import Semester, get_default_academic_year

# (requirement name, signatory role, order)
REQUIREMENTS = [
    ("Library Clearance", Roles.LIBRARIAN, 1),
    ("Cashier Clearance", Roles.CASHIER, 2),
    ("Adviser Approval", Roles.ADVISER, 3),
    ("Program Chair Approval", Roles.PROGRAM_CHAIR, 4),
    ("Governor Approval", Roles.GOVERNOR, 5),
    ("Dean Approval", Roles.DEAN, 6),
]

#: Students per institute given a clearance record at --scale 1.
PER_INSTITUTE = 20


class Command(SeedCommand):
    seed_name = "clearance"
    help = "Create demo clearance templates, requirements and student records."
    depends_on = ("seed_students", "seed_users")

    def add_seed_arguments(self, parser):
        parser.add_argument("--name", default="School Clearance")
        parser.add_argument("--academic-year", default=get_default_academic_year())
        parser.add_argument("--semester", default=Semester.FIRST.value)
        parser.add_argument(
            "--skip-students",
            action="store_true",
            help="Create the template and requirements but no student records.",
        )

    def reset(self, **options):
        StudentClearanceItem.objects.all().delete()
        StudentClearance.objects.all().delete()
        ClearanceRequirement.objects.all().delete()
        deleted, _ = ClearanceType.objects.all().delete()
        self.warn(f"deleted {deleted} clearance type row(s) and everything under them")

    def _actor_for(self, institute: Institute) -> User | None:
        return (
            User.objects.filter(
                institute=institute,
                groups__name__in=[Roles.CLEARANCE_ADMIN, Roles.SCHOOL_ADMIN],
            )
            .order_by("id")
            .first()
        )

    def _signatory_group(self, role: str) -> Group:
        # sync_role creates the group with its permissions if seed_rbac has
        # not run, so this seeder does not depend on running order.
        return sync_role(role)

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")
        academic_year = options["academic_year"]
        semester = options["semester"]

        created_types = 0
        created_requirements = 0
        created_clearances = 0
        created_items = 0

        for institute in institutes:
            actor = self._actor_for(institute)

            clearance_type, made = ClearanceType.objects.get_or_create(
                institute=institute,
                name=options["name"],
                academic_year=academic_year,
                semester=semester,
                defaults={
                    "description": "Seeded clearance template.",
                    "is_active": True,
                    "is_issued": False,
                    "target_year_levels": [1, 2, 3, 4],
                    "updated_by": actor,
                },
            )
            created_types += int(made)

            requirements = []
            for name, role, order in REQUIREMENTS:
                requirement, req_made = ClearanceRequirement.objects.get_or_create(
                    clearance_type=clearance_type,
                    name=name,
                    defaults={
                        "authorizing_role": self._signatory_group(role),
                        "order_sequence": order,
                        "updated_by": actor,
                    },
                )
                created_requirements += int(req_made)
                requirements.append(requirement)

            if options["skip_students"]:
                continue

            clearances, items = self._seed_students(
                clearance_type=clearance_type,
                requirements=requirements,
                actor=actor,
            )
            created_clearances += clearances
            created_items += items

            if clearances and not clearance_type.is_issued:
                clearance_type.is_issued = True
                clearance_type.issued_at = timezone.now()
                clearance_type.save(update_fields=["is_issued", "issued_at"])

        self.created("clearance type", created_types)
        self.created("clearance requirement", created_requirements)
        self.created("student clearance", created_clearances)
        self.created("clearance item", created_items)

    def _seed_students(self, *, clearance_type, requirements, actor):
        eligible = list(
            Student.objects.filter(
                s_status=Student.StudentStatus.ENROLLED,
                s_lvl__in=clearance_type.target_year_levels,
                program__institute_id=clearance_type.institute_id,
            )
            .exclude(
                id__in=StudentClearance.objects.filter(
                    clearance_type=clearance_type
                ).values_list("student_id", flat=True)
            )
            .order_by("id")[: PER_INSTITUTE * self.scale]
        )
        if not eligible or not requirements:
            return 0, 0

        # One signatory per role, so a cleared item names a plausible person.
        signatories = {
            requirement.pk: User.objects.filter(
                groups=requirement.authorizing_role
            ).first()
            for requirement in requirements
        }

        created_clearances = 0
        created_items = 0

        for student in eligible:
            clearance = StudentClearance.objects.create(
                student=student,
                clearance_type=clearance_type,
                status=StudentClearance.ClearanceStatus.PENDING,
                updated_by=actor,
            )
            created_clearances += 1

            # Clear a prefix of the requirements, so the demo has clearances
            # at every stage rather than all-pending.
            cleared_upto = self.rng.randint(0, len(requirements))
            items = []

            for index, requirement in enumerate(requirements):
                is_cleared = index < cleared_upto
                items.append(
                    StudentClearanceItem(
                        student_clearance=clearance,
                        requirement=requirement,
                        status=(
                            StudentClearanceItem.ItemStatus.CLEARED
                            if is_cleared
                            else StudentClearanceItem.ItemStatus.PENDING
                        ),
                        cleared_by_user=(
                            signatories.get(requirement.pk) if is_cleared else None
                        ),
                        cleared_at=timezone.now() if is_cleared else None,
                        updated_by=actor,
                    )
                )

            StudentClearanceItem.objects.bulk_create(items)
            created_items += len(items)

            if cleared_upto == len(requirements):
                clearance.status = StudentClearance.ClearanceStatus.COMPLETED
                clearance.date_completed = timezone.now()
                clearance.save(update_fields=["status", "date_completed"])
            elif cleared_upto:
                clearance.status = StudentClearance.ClearanceStatus.IN_PROGRESS
                clearance.save(update_fields=["status"])

        return created_clearances, created_items
