"""
Seed one demo account per role, in every institute, plus a superuser.

A username is *derived from the rows already in the database* rather than
listed here: the acronym of the school, the acronym of the institute, and the
role the account holds.

    <school>_<institute>_<role>   dnsc_ic_admin, dnsc_ic_cashier, um_cce_cmsadmin
    <school>_<role>               dnsc_admin, um_admin - school-wide accounts
    superadmin                    the one account that belongs to nowhere

Both halves come off the seeded rows, so a campus added to
`api/seeding/catalog.py` gets its accounts with no change here. The school
half is not decoration either: `IC` at one school and `IC` at another are
different institutes, and a bare `icadmin` would have to belong to one of
them.

For the Institute of Computing at DNSC that reads:

    dnsc_ic_admin              Institute Admins - runs the institute
    dnsc_ic_staff              Institute Staffs - its day-to-day records
    dnsc_ic_cmsadmin / _cmsstaff        Collection Management System
    dnsc_ic_clearanceadmin              Clearance Management System
    dnsc_ic_lockeradmin / _lockerstaff  Locker Management System
    dnsc_ic_librarian, _dean, _cashier, _adviser, _programchair, _governor
                               clearance signatories

Every institute gets that same set under its own pair. Without one set per
institute, half the institutes end up with no collection staff and the fee,
payment and clearance seeders quietly skip them.

`--essential` cuts it to the accounts a new deployment cannot be set up
without: the superuser, a School Admin per school, an Institute Admin per
institute. The system accounts - collection, clearance, lockers, the
signatories - are sample data. Their roles still exist after `seed_rbac`, so
an admin creates the real ones through the API rather than inheriting a pile
of logins with a shared password.

An institute whose acronym is blank falls back to `inst1`, `inst2`, ... and
says so; so does a school with no short name.

Student accounts are created by `seed_students`, alongside their records.

These credentials are for local and staging use: the command refuses to run
with DEBUG off unless `--allow-production` is passed, and `--password`
overrides the default.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.core.management.base import CommandError

from api.institutes.models import Institute
from api.rbac.roles import ROLE_DEFINITIONS, Roles
from api.rbac.services import assign_role
from api.schools.models import School
from api.seeding.support import SeedCommand
from api.users.models import User

DEFAULT_PASSWORD = "password123"


@dataclass(frozen=True)
class AccountSpec:
    #: Last part of the username: `dnsc_ic_` + `cashier`.
    suffix: str
    first_name: str
    last_name: str
    role: str
    #: Created even in an `--essential` run. Somebody has to be able to sign
    #: in and set the rest up; nobody has to be able to sign in as a demo
    #: librarian.
    essential: bool = False


#: One set per institute, each prefixed with `<school>_<institute>_`.
PER_INSTITUTE_ACCOUNTS = (
    AccountSpec("admin", "Alex", "Aparece", Roles.INSTITUTE_ADMIN, essential=True),
    AccountSpec("staff", "Jessa", "Terig", Roles.INSTITUTE_STAFF),
    AccountSpec("clearanceadmin", "Noel", "Cruz", Roles.CLEARANCE_ADMIN),
    AccountSpec("librarian", "Grace", "Lim", Roles.LIBRARIAN),
    AccountSpec("dean", "Micah", "Floro", Roles.DEAN),
    AccountSpec("cashier", "Paz", "Domingo", Roles.CASHIER),
    AccountSpec("adviser", "Ivan", "Trinidad", Roles.ADVISER),
    AccountSpec("programchair", "Cora", "Aguilar", Roles.PROGRAM_CHAIR),
    AccountSpec("governor", "Dante", "Rosario", Roles.GOVERNOR),
)

#: Accounts that belong to a whole school rather than to one institute,
#: prefixed with `<school>_` alone.
PER_SCHOOL_ACCOUNTS = (
    AccountSpec("admin", "Sofia", "Reyes", Roles.SCHOOL_ADMIN, essential=True),
)

#: What these accounts were called before usernames were derived from the
#: seeded rows. Only `--reset` uses them, so that a database seeded by an
#: older scheme is cleaned up rather than left holding two sets of logins.
LEGACY_BASES = (
    "schooladmin",
    "instituteadmin",
    "institutestaff",
    "cmsadmin",
    "cmsstaff",
    "clearanceadmin",
    "lockeradmin",
    "lockerstaff",
    "librarian",
    "dean",
    "cashier",
    "adviser",
    "programchair",
    "governor",
)


def slug(acronym: str) -> str:
    """`IC` -> `ic`. Anything that is not a letter or digit is dropped."""
    return "".join(character for character in acronym if character.isalnum()).lower()


class Command(SeedCommand):
    seed_name = "users"
    help = "Create one demo account per role per institute, plus a superuser."
    depends_on = ("seed_rbac", "seed_institutes")

    def add_seed_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help=f"Password for every seeded account (default {DEFAULT_PASSWORD}).",
        )
        parser.add_argument(
            "--essential",
            action="store_true",
            help=(
                "Only the accounts a new deployment needs: the superuser, a "
                "School Admin per school, an Institute Admin per institute."
            ),
        )
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Required to run with DEBUG off.",
        )

    # -- entry points -----------------------------------------------------

    def reset(self, **options):
        institutes = list(Institute.objects.select_related("school").order_by("pk"))
        schools = list(School.objects.order_by("pk"))

        school_prefixes = self._school_prefixes(schools)
        institute_prefixes = self._prefixes(institutes, school_prefixes, quiet=True)

        usernames = ["superadmin"]
        for prefix in institute_prefixes.values():
            usernames += [f"{prefix}_{spec.suffix}" for spec in PER_INSTITUTE_ACCOUNTS]
        for prefix in school_prefixes.values():
            usernames += [f"{prefix}_{spec.suffix}" for spec in PER_SCHOOL_ACCOUNTS]

        usernames += self._superseded_usernames(institutes, schools)

        deleted, _ = User.objects.filter(username__in=usernames).delete()
        self.warn(f"deleted {deleted} seeded account row(s)")

    def seed(self, **options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "seed_users creates accounts with a shared, well-known "
                "password. Refusing to run with DEBUG off - pass "
                "--allow-production if you really mean it."
            )

        password = options["password"]
        essential_only = options.get("essential", False)

        # `require` for the "run seed_institutes first" message; the second
        # read is the one used, with the school in hand for the prefixes.
        self.require(Institute, "seed_institutes")
        institutes = list(Institute.objects.select_related("school").order_by("pk"))

        schools: list[School] = []
        for institute in institutes:
            if institute.school is not None and institute.school not in schools:
                schools.append(institute.school)

        school_accounts = self._wanted(PER_SCHOOL_ACCOUNTS, essential_only)
        institute_accounts = self._wanted(PER_INSTITUTE_ACCOUNTS, essential_only)

        created = self._account(
            username="superadmin",
            first_name="Super",
            last_name="Admin",
            institute=institutes[0],
            school=institutes[0].school,
            password=password,
            role=None,
            extra={"is_staff": True, "is_superuser": True},
        )
        self.created("superuser", int(created))

        made = 0
        school_prefixes = self._school_prefixes(schools)

        # School-wide accounts carry no institute on purpose: a School Admins
        # account is school-wide, and pinning it to one institute would scope
        # it to that institute's rows.
        for school, prefix in school_prefixes.items():
            for spec in school_accounts:
                made += int(
                    self._account(
                        username=f"{prefix}_{spec.suffix}",
                        first_name=spec.first_name,
                        last_name=spec.last_name,
                        institute=None,
                        school=school,
                        password=password,
                        role=spec.role,
                    )
                )

        for institute, prefix in self._prefixes(institutes, school_prefixes).items():
            for spec in institute_accounts:
                made += int(
                    self._account(
                        username=f"{prefix}_{spec.suffix}",
                        first_name=spec.first_name,
                        last_name=spec.last_name,
                        institute=institute,
                        school=institute.school,
                        password=password,
                        role=spec.role,
                    )
                )

            shown = ", ".join(
                f"{prefix}_{spec.suffix}" for spec in institute_accounts[:2]
            )
            self.info(
                f"{institute.institute_name}: {shown}"
                + (", ..." if len(institute_accounts) > 2 else "")
            )

        self.created("role account", made)
        if essential_only:
            self.info(
                "--essential: the collection, clearance, locker and signatory "
                "accounts were skipped. Their roles exist, so an admin can "
                "create the real ones."
            )
        self.info(
            f"{len(institutes)} institute(s) covered; "
            f"password for every seeded account: {password}"
        )

    # -- usernames --------------------------------------------------------

    def _wanted(
        self, specs: tuple[AccountSpec, ...], essential_only: bool
    ) -> tuple[AccountSpec, ...]:
        if not essential_only:
            return specs
        return tuple(spec for spec in specs if spec.essential)

    def _school_prefixes(self, schools) -> dict:
        """`dnsc` for each school, keyed by the school."""
        prefixes: dict = {}
        taken: set[str] = set()

        for index, school in enumerate(schools):
            base = slug(school.short_name or "") or f"school{index + 1}"
            prefixes[school] = self._unique(base, taken)

        return prefixes

    def _prefixes(
        self, institutes, school_prefixes: dict, *, quiet: bool = False
    ) -> dict:
        """`dnsc_ic` for each institute, keyed by the institute.

        The school's prefix and the institute's acronym, both lowercased. A
        blank acronym falls back to a numbered `inst1`, reported as it goes -
        a username nobody can guess from the acronym is worth a line of
        output. `quiet` is for `--reset`, which works the names out again and
        would otherwise say it all twice.
        """
        prefixes: dict = {}
        taken: set[str] = set()

        for index, institute in enumerate(institutes):
            base = slug(institute.acronym)
            if not base:
                base = f"inst{index + 1}"
                if not quiet:
                    self.warn(
                        f"{institute.institute_name} has no acronym; "
                        f"its accounts are named ..._{base}_admin"
                    )

            school_prefix = school_prefixes.get(institute.school) or (
                slug(getattr(institute.school, "short_name", "") or "") or "school"
            )
            prefixes[institute] = self._unique(f"{school_prefix}_{base}", taken)

        return prefixes

    def _unique(self, base: str, taken: set[str]) -> str:
        candidate, suffix = base, 2
        while candidate in taken:
            candidate = f"{base}{suffix}"
            suffix += 1
        taken.add(candidate)
        return candidate

    def _superseded_usernames(self, institutes, schools) -> list[str]:
        """Names the older schemes gave these same accounts.

        Two of them: the original fixed names (`instituteadmin`, `cmsadmin2`)
        and the acronym-only pass after it (`icadmin`, `dnscadmin`). `--reset`
        deletes those as well, so re-seeding does not leave a database holding
        three generations of logins for one person.
        """
        names: list[str] = []

        for institute in institutes:
            acronym = slug(institute.acronym)
            if acronym:
                names += [f"{acronym}{spec.suffix}" for spec in PER_INSTITUTE_ACCOUNTS]

        for school in schools:
            acronym = slug(school.short_name or "")
            if acronym:
                names += [f"{acronym}{spec.suffix}" for spec in PER_SCHOOL_ACCOUNTS]

        span = max(len(institutes), 1) + 1
        names += [
            base if index == 0 else f"{base}{index + 1}"
            for base in LEGACY_BASES
            for index in range(span)
        ]
        return names

    # -- rows -------------------------------------------------------------

    def _account(
        self,
        *,
        username: str,
        first_name: str,
        last_name: str,
        institute: Institute | None,
        school,
        password: str,
        role: str | None,
        extra: dict | None = None,
    ) -> bool:
        if role is not None and role not in ROLE_DEFINITIONS:
            self.warn(f"skipping {username}: role {role!r} is not declared")
            return False

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@example.edu",
                "first_name": first_name,
                "last_name": last_name,
                "institute": institute,
                "school": school,
                **(extra or {}),
            },
        )
        if created:
            user.set_password(password)
            # pepper_version must land with the hash it describes, or the
            # row claims a legacy hash it does not have and the account
            # cannot sign in. User.save() now enforces the pairing; listed
            # anyway so the requirement is visible where it was once missed.
            user.save(update_fields=["password", "pepper_version"])

        if role is not None:
            assign_role(user, role)

        return created
