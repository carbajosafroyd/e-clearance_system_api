"""Seed lockers, subscription requests, subscriptions and locker reports."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from api.locker_system.models import (
    Locker,
    LockerReport,
    LockerSubscription,
    LockerSubscriptionRequest,
)
from api.institutes.models import Institute
from api.schools.models import SchoolBuilding
from api.seeding.support import SeedCommand
from api.students.models import Student
from core.choices import Semester, get_default_academic_year

#: Lockers per floor of each building, at --scale 1.
PER_FLOOR = 6

#: Share of students in an institute who ask for a locker.
REQUEST_SHARE = 0.25

REPORT_MESSAGES = [
    "Locker door will not close properly.",
    "Lock is jammed.",
    "Someone else's belongings are inside.",
    "Hinge is loose.",
]


class Command(SeedCommand):
    seed_name = "lockers"
    help = "Create demo lockers, requests, subscriptions and reports."
    depends_on = ("seed_schools", "seed_students")

    def reset(self, **options):
        LockerReport.objects.all().delete()
        LockerSubscription.objects.all().delete()
        LockerSubscriptionRequest.objects.all().delete()
        deleted, _ = Locker.objects.all().delete()
        self.warn(f"deleted {deleted} locker row(s) and everything under them")

    def seed(self, **options):
        buildings = self.require(SchoolBuilding, "seed_schools")
        institutes = self.require(Institute, "seed_institutes")

        created = 0
        for building in buildings:
            institute = self.pick(
                [i for i in institutes if i.school_id == building.school_id]
                or institutes
            )
            for floor in range(1, building.max_floor + 1):
                for slot in range(1, PER_FLOOR * self.scale + 1):
                    # Locker numbers are unique across the whole table, so
                    # the building id is part of the number.
                    number = f"B{building.pk}-{floor}{slot:02d}"
                    _, made = Locker.objects.get_or_create(
                        locker_number=number,
                        defaults={
                            "building": building,
                            "institute": institute,
                            "floor": floor,
                            # A few out of service, so the metrics endpoint
                            # has a non-zero maintenance count.
                            "is_active": not self.chance(0.05),
                        },
                    )
                    created += int(made)

        self.created("locker", created)
        self._seed_subscriptions()
        self._seed_reports()

    def _seed_subscriptions(self):
        # The lockers above are reference data and get_or_create cleanly;
        # requests and subscriptions are activity, so they are seeded once.
        if LockerSubscriptionRequest.objects.exists():
            self.info("locker requests already present; nothing to do (use --reset)")
            return

        academic_year = get_default_academic_year()
        due = timezone.now() + timedelta(days=7)

        requests_made = 0
        subscriptions_made = 0

        for institute in Institute.objects.all():
            lockers = list(
                Locker.objects.filter(institute=institute, is_active=True).order_by("id")
            )
            students = list(
                Student.objects.filter(program__institute=institute).order_by("id")
            )
            if not lockers or not students:
                continue

            wanted = min(
                len(lockers), max(1, int(len(students) * REQUEST_SHARE))
            )
            applicants = self.sample(students, wanted)

            for locker, student in zip(lockers, applicants):
                if LockerSubscriptionRequest.objects.filter(
                    student=student,
                    academic_year=academic_year,
                    semester=Semester.FIRST,
                ).exists():
                    continue

                approved = self.chance(0.7)
                request = LockerSubscriptionRequest.objects.create(
                    student=student,
                    locker=locker,
                    academic_year=academic_year,
                    semester=Semester.FIRST,
                    status=(
                        LockerSubscriptionRequest.SubscriptionStatus.APPROVED
                        if approved
                        else LockerSubscriptionRequest.SubscriptionStatus.PENDING
                    ),
                )
                requests_made += 1

                if not approved:
                    continue

                paid = self.chance(0.6)
                LockerSubscription.objects.create(
                    student=student,
                    locker=locker,
                    academic_year=academic_year,
                    semester=Semester.FIRST,
                    status=LockerSubscription.SubscriptionStatus.ACTIVE,
                    payment_status=(
                        LockerSubscription.SubscriptionPaymentStatus.PAID
                        if paid
                        else LockerSubscription.SubscriptionPaymentStatus.UNPAID
                    ),
                    payment_due_at=due,
                    paid_at=timezone.now() if paid else None,
                    subscription_request=request,
                )
                subscriptions_made += 1

        self.created("locker subscription request", requests_made)
        self.created("locker subscription", subscriptions_made)

    def _seed_reports(self):
        subscriptions = list(
            LockerSubscription.objects.select_related("student", "locker").order_by("id")
        )
        if not subscriptions:
            return

        created = 0
        for subscription in self.sample(
            subscriptions, max(1, len(subscriptions) // 6)
        ):
            _, made = LockerReport.objects.get_or_create(
                locker=subscription.locker,
                student=subscription.student,
                defaults={"message": self.pick(REPORT_MESSAGES)},
            )
            created += int(made)

        self.created("locker report", created)
