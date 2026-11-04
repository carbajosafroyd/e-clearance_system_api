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

TEAM_STUDENTS = [
    {
        "student_id": "202400633",
        "first_name": "Froyd",
        "middle_name": "D",
        "last_name": "Carbajosa",
    },
    {
        "student_id": "202401033",
        "first_name": "Chieruf Jhann",
        "middle_name": "E",
        "last_name": "Pastedeo",
    },
    {
        "student_id": "202401164",
        "first_name": "Shane Angelo",
        "middle_name": "C",
        "last_name": "Garcia",
    },
    {
        "student_id": "202301626",
        "first_name": "Erick Cyle",
        "middle_name": "A",
        "last_name": "Embodo",
    },
    {
        "student_id": "202401177",
        "first_name": "Cyrus",
        "middle_name": "M",
        "last_name": "Suelto",
    },
    {
        "student_id": "202400928",
        "first_name": "Roosevelt",
        "middle_name": "O",
        "last_name": "Gulanes",
    },
]

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

        bsit_program = Program.objects.filter(acronym="BSIT").first()
        if not bsit_program:
            bsit_program = programs[0]

        created_students = 0
        created_users = 0

        for info in TEAM_STUDENTS:
            student_id = info["student_id"]
            first_name = info["first_name"]
            middle_name = info["middle_name"]
            last_name = info["last_name"]
            username = f"Student{student_id}"

            # Check if student already exists
            existing_student = Student.objects.filter(s_studentID=student_id).first()
            if existing_student:
                continue

            user = None
            if want_accounts:
                user = User.objects.filter(username=username).first()
                if not user:
                    user = User.objects.create_user(
                        username=username,
                        email=f"{student_id}@students.dnsc.edu.ph",
                        password=password,
                        first_name=first_name,
                        last_name=last_name,
                        institute=bsit_program.institute,
                        school=bsit_program.institute.school,
                    )
                    assign_role(user, Roles.STUDENT)
                    created_users += 1

            Student.objects.create(
                s_studentID=student_id,
                s_rfid=student_id,
                program=bsit_program,
                user=user,
                s_fname=first_name,
                s_mname=middle_name,
                s_lname=last_name,
                s_set="C",
                s_lvl=3,
                s_status=Student.StudentStatus.ENROLLED,
            )
            created_students += 1

        self.created("student", created_students)
        self.created("student account", created_users)
