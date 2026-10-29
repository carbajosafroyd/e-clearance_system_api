"""
Seed point policies, student contributions and a few redeemed discounts.

Everything goes through `contributions.services`, so the wallets, the ledger
and the fee balances agree with each other - a seeded leaderboard is one you
could have earned.
"""

from __future__ import annotations

from decimal import Decimal

from api.contributions.models import (
    Contribution,
    ContributionImpact,
    ContributionKind,
    FeeDiscount,
    PointAccount,
    PointPolicy,
    PointTransaction,
)
from api.contributions import services
from api.fees.models import Fee
from api.institutes.models import Institute
from api.rbac.roles import Roles
from api.seeding.support import SeedCommand
from api.students.models import Student
from api.users.models import User
from core import features
from core.choices import SystemScope

#: (system, kind, impact, title, description)
REPORTS = [
    (
        SystemScope.COLLECTION,
        ContributionKind.BUG_REPORT,
        ContributionImpact.HIGH,
        "Fee list shows the wrong balance after a partial payment",
        "After paying half of my tuition fee at the counter, the fee list "
        "still showed the full amount until I signed out and back in. The "
        "receipt was correct, only the list was stale.",
    ),
    (
        SystemScope.COLLECTION,
        ContributionKind.RECOMMENDATION,
        ContributionImpact.MEDIUM,
        "Show the due date on the mobile fee card",
        "On a phone the fee card only shows the amount. Adding the due date "
        "would save opening each fee to check which one is closest to lapsing.",
    ),
    (
        SystemScope.COLLECTION,
        ContributionKind.IMPROVEMENT,
        ContributionImpact.LOW,
        "Let me filter my payment history by semester",
        "My payment history is one long list. A semester filter, like the "
        "fees page already has, would make it usable by third year.",
    ),
    (
        SystemScope.LOCKER,
        ContributionKind.BUG_REPORT,
        ContributionImpact.CRITICAL,
        "Cancelled locker request still blocks the locker",
        "I cancelled my request for locker B2-104 and it never went back to "
        "available, so nobody else could take it for the whole semester.",
    ),
    (
        SystemScope.LOCKER,
        ContributionKind.FEATURE_REQUEST,
        ContributionImpact.MEDIUM,
        "Notify me before my locker subscription expires",
        "A reminder a week before expiry would stop people losing their "
        "locker over a date they forgot.",
    ),
    (
        SystemScope.ATTENDANCE,
        ContributionKind.DATA_CORRECTION,
        ContributionImpact.MEDIUM,
        "Afternoon check-out was recorded on the wrong day",
        "My Foundation Week afternoon check-out on day two was filed under "
        "day three, so day two shows as a missed slot and was charged.",
    ),
    (
        SystemScope.ATTENDANCE,
        ContributionKind.RECOMMENDATION,
        ContributionImpact.HIGH,
        "Show which slot I missed, not just the total fine",
        "The attendance fee shows one number. Showing the day and the slot "
        "behind it would let students check it against their own records "
        "instead of queueing at the office.",
    ),
    (
        SystemScope.CLEARANCE,
        ContributionKind.IMPROVEMENT,
        ContributionImpact.MEDIUM,
        "Order clearance signatories the way we actually visit them",
        "The clearance list is alphabetical, but the offices have to be "
        "visited in sequence. Ordering it that way would stop people "
        "walking back and forth.",
    ),
    (
        SystemScope.PLATFORM,
        ContributionKind.OTHER,
        ContributionImpact.LOW,
        "Typo on the sign-in screen",
        "The sign-in screen says 'Enter you student number'. Should be 'your'.",
    ),
]


class Command(SeedCommand):
    seed_name = "contributions"
    help = "Create demo point policies, contributions and fee discounts."
    depends_on = ("seed_students", "seed_fees", "seed_users")
    feature = features.CONTRIBUTIONS

    def reset(self, **options):
        PointTransaction.objects.all().delete()
        FeeDiscount.objects.all().delete()
        PointAccount.objects.all().delete()
        Contribution.objects.all().delete()
        deleted, _ = PointPolicy.objects.all().delete()
        self.warn(f"deleted {deleted} point policy row(s) and everything under them")

    def _reviewer_for(self, institute: Institute) -> User | None:
        return (
            User.objects.filter(
                institute=institute,
                groups__name__in=[Roles.CMS_ADMIN, Roles.SCHOOL_ADMIN],
            )
            .order_by("id")
            .first()
        )

    def seed(self, **options):
        institutes = self.require(Institute, "seed_institutes")

        # Reports are the whole module when awards are switched off, so the
        # policies and the redemptions they fund are seeded only where the
        # points half is actually running.
        pays_out = features.is_enabled(features.CONTRIBUTION_POINTS)
        if pays_out:
            self._seed_policies(institutes)
        else:
            self.info("awards are switched off; seeding reports only")

        if Contribution.objects.exists():
            self.info("contributions already present; nothing to do (use --reset)")
            return

        for institute in institutes:
            reviewer = self._reviewer_for(institute)
            self._seed_contributions(institute, reviewer)

        if pays_out:
            self._seed_discounts()

    def _seed_policies(self, institutes) -> None:
        created = 0
        for institute in institutes:
            for system, _label in SystemScope.choices:
                policy, made = PointPolicy.objects.get_or_create(
                    institute=institute,
                    system=system,
                    defaults={
                        # A point is worth a peso, so the numbers on the
                        # screen are easy to reason about while testing.
                        "peso_per_point": Decimal("1.00"),
                        "min_points_per_redemption": 10,
                        "max_points_per_redemption": 500,
                        "max_discount_percent": Decimal("50.00"),
                    },
                )
                created += int(made)
        self.created("point policy", created)

    def _seed_contributions(self, institute: Institute, reviewer: User | None) -> None:
        students = list(
            Student.objects.filter(program__institute=institute).order_by("id")
        )
        if not students:
            return

        counts = {"pending": 0, "under_review": 0, "approved": 0, "rejected": 0}

        for index in range(len(REPORTS) * self.scale):
            system, kind, impact, title, description = REPORTS[index % len(REPORTS)]
            student = self.pick(students)

            contribution = Contribution.objects.create(
                student=student,
                system=system,
                kind=kind,
                impact=impact,
                title=title,
                description=description,
            )

            roll = self.rng.random()
            if reviewer is None or roll < 0.25:
                counts["pending"] += 1
                continue

            if roll < 0.35:
                contribution.status = Contribution.Status.UNDER_REVIEW
                contribution.reviewed_by = reviewer
                contribution.save(update_fields=["status", "reviewed_by"])
                counts["under_review"] += 1
                continue

            if roll < 0.5:
                services.reject_contribution(
                    contribution=contribution,
                    actor=reviewer,
                    remarks=(
                        "Thanks for the report - this is working as designed. "
                        "Please raise it with the collection office if it is "
                        "still causing you trouble."
                    ),
                )
                counts["rejected"] += 1
                continue

            services.approve_contribution(
                contribution=contribution,
                actor=reviewer,
                remarks="Reproduced and queued for the next release. Thank you.",
            )
            counts["approved"] += 1

        for state, count in counts.items():
            if count:
                self.created(f"{state} contribution", count)

    def _seed_discounts(self) -> None:
        """Spend some of the earned points, and approve most of what is spent."""
        accounts = list(
            PointAccount.objects.select_related("student__program")
            .filter(balance__gte=10)
            .order_by("id")
        )
        if not accounts:
            self.info("no student has enough points to redeem yet")
            return

        requested = 0
        approved = 0
        rejected = 0

        for account in accounts:
            fee = (
                Fee.objects.filter(
                    student=account.student,
                    status__in=[Fee.FeeStatus.PENDING, Fee.FeeStatus.OVERDUE],
                    balance__gt=0,
                )
                .order_by("-balance")
                .first()
            )
            if fee is None:
                continue

            reviewer = self._reviewer_for(account.student.program.institute)
            if reviewer is None:
                continue

            # Spend a round slice of the balance, inside the policy bounds.
            points = min(account.balance, 10 * (account.balance // 10))
            if points < 10:
                continue

            quote = services.discount_quote(
                fee=fee, system=account.system, points=points
            )
            if quote["amount"] > quote["max_amount"]:
                # Scale back to what this fee allows, rounded down to ten.
                affordable = int(
                    quote["max_amount"] / max(quote["peso_per_point"], Decimal("0.01"))
                )
                points = 10 * (affordable // 10)
                if points < 10:
                    continue

            try:
                discount = services.request_discount(
                    fee=fee,
                    student=account.student,
                    system=account.system,
                    points=points,
                    actor=account.student.user or reviewer,
                    remarks="Seeded example: redeeming contribution points.",
                )
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                self.warn(f"skipped a redemption for {account.student}: {exc}")
                continue

            requested += 1

            roll = self.rng.random()
            if roll < 0.2:
                continue  # left pending for the review screen
            if roll < 0.3:
                services.reject_discount(
                    discount=discount,
                    actor=reviewer,
                    remarks="Seeded example: fee already settled at the counter.",
                )
                rejected += 1
                continue

            services.approve_discount(
                discount=discount,
                actor=reviewer,
                remarks="Seeded example: points verified against the ledger.",
            )
            approved += 1

        self.created("discount request", requested)
        self.created("approved discount", approved)
        self.created("rejected discount", rejected)
