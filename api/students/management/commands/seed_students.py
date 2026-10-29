"""
Seed students and the user accounts they sign in with.

Student IDs are `YYYY` + a six-digit sequence, which satisfies the model's
10-character rule and reads like the real thing. Usernames follow the same
number, so `student2026000001` is the account for student ID `2026000001`.
"""

from __future__ import annotations

from api.programs.models import Program
from api.rbac.roles import Roles
from api.rbac.services import assign_role
from api.seeding.support import SeedCommand
from api.students.models import Student
from api.users.models import User

FIRST_NAMES = [
    "Ana", "Ben", "Carlo", "Dina", "Elmer", "Fe", "Gil", "Hazel",
    "Ivan", "Joy", "Ken", "Lea", "Mon", "Nica", "Omar", "Pia",
]
LAST_NAMES = [
    "Abad", "Bautista", "Castro", "Dizon", "Espino", "Flores", "Garcia",
    "Hernandez", "Ilagan", "Jimenez", "Katigbak", "Lorenzo", "Mercado",
    "Navarro", "Ocampo", "Pascual",
]
SETS = ["A", "B", "C"]

#: Students created per program at --scale 1.
PER_PROGRAM = 8

DEFAULT_PASSWORD = "password123"


class Command(SeedCommand):
    seed_name = "students"
    help = "Create demo students, each with a linked user account."
    depends_on = ("seed_programs", "seed_rbac")

    def add_seed_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help=f"Password for every seeded student (default {DEFAULT_PASSWORD}).",
        )
        parser.add_argument(
            "--no-accounts",
            action="store_true",
            help="Create student records without user accounts.",
        )

    def reset(self, **options):
        usernames = list(
            Student.objects.exclude(user=None).values_list("user__username", flat=True)
        )
        deleted, _ = Student.objects.all().delete()
        User.objects.filter(username__in=usernames).delete()
        self.warn(f"deleted {deleted} student row(s) and their accounts")

    def seed(self, **options):
        programs = self.require(Program, "seed_programs")
        password = options["password"]
        want_accounts = not options["no_accounts"]

        # Continue the ID sequence rather than restarting it, so a second run
        # with a bigger --scale adds students instead of colliding.
        sequence = Student.objects.count()

        created_students = 0
        created_users = 0

        for program in programs:
            # Top up to the target rather than always adding a fresh batch -
            # otherwise `seed_all` twice gives you twice the student body,
            # and every downstream seeder doubles with it.
            target = PER_PROGRAM * self.scale
            existing = Student.objects.filter(program=program).count()

            for _ in range(max(0, target - existing)):
                sequence += 1
                student_id = f"2026{sequence:06d}"

                if Student.objects.filter(s_studentID=student_id).exists():
                    continue

                first_name = self.pick(FIRST_NAMES)
                last_name = self.pick(LAST_NAMES)

                user = None
                if want_accounts:
                    user = User.objects.create_user(
                        username=f"student{student_id}",
                        email=f"{student_id}@students.example.edu",
                        password=password,
                        first_name=first_name,
                        last_name=last_name,
                        institute=program.institute,
                        school=program.institute.school,
                    )
                    assign_role(user, Roles.STUDENT)
                    created_users += 1

                Student.objects.create(
                    s_studentID=student_id,
                    # The model caps rfid at 10 characters and requires all
                    # ten, so it mirrors the student ID rather than inventing
                    # a longer tag.
                    s_rfid=student_id,
                    program=program,
                    user=user,
                    s_fname=first_name,
                    s_lname=last_name,
                    s_set=self.pick(SETS),
                    s_lvl=self.rng.randint(1, 4),
                    s_status=Student.StudentStatus.ENROLLED,
                )
                created_students += 1

        self.created("student", created_students)
        self.created("student account", created_users)
