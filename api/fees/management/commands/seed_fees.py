"""
Seed fees by running the real generation engine.

Deliberately not `Fee.objects.bulk_create(...)`: going through
`services.generation.engine.generate` means the seeded data has the same
shape production data does - batches linked to their fees, exemptions
honoured as waivers, attendance fees priced from the actual missing slots.
It also means this command fails if generation is broken, which is a useful
thing for a seeder to do.

The notification side of generation is skipped: `runner._execute` is what
sends those, and a seed run would otherwise leave every demo student with a
"your fees are ready" notification from a fee they never saw appear.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from api.collection_categories.models import CollectionCategory
from api.fees.constants import ATTENDANCE_CATEGORY_NAME
from api.fees.models import Fee, GeneratedFeeBatch
from api.fees.services.generation.attendance_fees import AttendanceFeeGeneration
from api.fees.services.generation.engine import generate
from api.fees.services.generation.general_fees import GeneralFeeGeneration
from api.fees.services.generation.types import GenerationRequest
from api.institutes.models import Institute
from api.institutes_attendance_events.models import InstituteAttendanceEvent
from api.rbac.roles import Roles
from api.seeding.support import SeedCommand
from api.users.models import User
from core.choices import Semester, get_default_academic_year


class Command(SeedCommand):
    seed_name = "fees"
    help = "Generate demo fees through the real fee-generation engine."
    depends_on = ("seed_collection_categories", "seed_attendance")

    def add_seed_arguments(self, parser):
        parser.add_argument(
            "--skip-attendance",
            action="store_true",
            help="Only generate the flat collection fees.",
        )

    def reset(self, **options):
        Fee.objects.all().delete()
        deleted, _ = GeneratedFeeBatch.objects.all().delete()
        self.warn(f"deleted {deleted} generated batch row(s) and their fees")

    def _actor_for(self, institute: Institute) -> User | None:
        """Someone in this institute who could plausibly have run generation."""
        return (
            User.objects.filter(
                institute=institute,
                groups__name__in=[Roles.CMS_ADMIN, Roles.SCHOOL_ADMIN],
            )
            .order_by("id")
            .first()
            or User.objects.filter(institute=institute, is_staff=True)
            .order_by("id")
            .first()
        )

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")
        academic_year = get_default_academic_year()
        due_date = timezone.now() + timedelta(days=14)

        batches_before = GeneratedFeeBatch.objects.count()
        fees_before = Fee.objects.count()

        for institute in institutes:
            actor = self._actor_for(institute)
            if actor is None:
                self.warn(
                    f"{institute.institute_name}: no staff account, skipping. "
                    "Run `python manage.py seed_users` first."
                )
                continue

            self._general_fees(
                institute=institute,
                actor=actor,
                academic_year=academic_year,
                due_date=due_date,
            )

            if not options["skip_attendance"]:
                self._attendance_fees(
                    institute=institute, actor=actor, due_date=due_date
                )

        self.created("fee batch", GeneratedFeeBatch.objects.count() - batches_before)
        self.created("fee", Fee.objects.count() - fees_before)

        # A handful of past-due fees so the overdue sweep has something to
        # find and the dashboard's red numbers are not always zero.
        overdue = self._backdate_some_fees()
        if overdue:
            self.info(f"{overdue} fee(s) back-dated so they are already past due")

    def _general_fees(self, *, institute, actor, academic_year, due_date):
        categories = CollectionCategory.objects.filter(institute=institute).exclude(
            category_name=ATTENDANCE_CATEGORY_NAME
        )

        for category in categories:
            outcome = generate(
                actor=actor,
                strategy=GeneralFeeGeneration(
                    category=category,
                    academic_year=academic_year,
                    semester=Semester.FIRST,
                ),
                request=GenerationRequest(
                    due_date=due_date,
                    all_students=True,
                    # Exemptions exist in the seed data; honouring them here
                    # is what makes the waived rows appear.
                    apply_exemptions=True,
                ),
            )
            if outcome.added:
                self.info(
                    f"{institute.institute_name} / {category.category_name}: "
                    f"{len(outcome.added)} fee(s), "
                    f"{outcome.waived_count} waived"
                )

    def _attendance_fees(self, *, institute, actor, due_date):
        events = InstituteAttendanceEvent.objects.filter(
            institute=institute
        ).prefetch_related("settings")

        for event in events:
            outcome = generate(
                actor=actor,
                strategy=AttendanceFeeGeneration(
                    category=CollectionCategory.objects.filter(
                        institute=institute, category_name=ATTENDANCE_CATEGORY_NAME
                    ).first(),
                    event=event,
                ),
                request=GenerationRequest(due_date=due_date, all_students=True),
            )
            if outcome.added:
                self.info(
                    f"{institute.institute_name} / {event.attendance_event.event_name}: "
                    f"{len(outcome.added)} attendance fee(s)"
                )

    def _backdate_some_fees(self) -> int:
        now = timezone.now()

        # Already done on a previous run - re-running would drag another
        # slice of fees into the past every time.
        if Fee.objects.filter(
            status=Fee.FeeStatus.PENDING, due_date__lt=now
        ).exists():
            return 0

        pending = list(
            Fee.objects.filter(status=Fee.FeeStatus.PENDING).order_by("id")
        )
        if not pending:
            return 0

        chosen = self.sample(pending, max(1, len(pending) // 10))
        past = now - timedelta(days=7)
        return Fee.objects.filter(id__in=[fee.pk for fee in chosen]).update(
            due_date=past
        )
