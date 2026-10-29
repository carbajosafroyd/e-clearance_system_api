"""
Tests for contributions, points and fee discounts.

The two things worth protecting here are the ledger invariant
(`balance == total_earned - total_spent`, and the transactions add up to it)
and the money identity (an approved discount must not look like collected
cash), because both fail silently.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from api.fees.models import Fee
from api.fees.tests.factories import (
    make_category,
    make_fee,
    make_institute,
    make_program,
    make_staff,
    make_student,
)
from api.rbac.roles import Roles
from core.choices import SystemScope

from . import selectors, services
from .models import (
    Contribution,
    ContributionImpact,
    ContributionKind,
    FeeDiscount,
    PointAccount,
    PointPolicy,
    PointTransaction,
)


def make_contribution(student, **overrides) -> Contribution:
    defaults = {
        "system": SystemScope.COLLECTION,
        "kind": ContributionKind.BUG_REPORT,
        "impact": ContributionImpact.MEDIUM,
        "title": "Fee list shows a stale balance",
        "description": "The list keeps the old balance until I sign out.",
    }
    defaults.update(overrides)
    return Contribution.objects.create(student=student, **defaults)


class PointLedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.student = make_student(cls.program)
        cls.reviewer = make_staff(cls.institute)

    def test_approving_awards_the_policy_amount(self):
        contribution = make_contribution(self.student)

        approved = services.approve_contribution(
            contribution=contribution, actor=self.reviewer
        )

        # bug_report (30) at medium impact (x1.0)
        self.assertEqual(approved.points_awarded, 30)
        account = PointAccount.objects.get(
            student=self.student, system=SystemScope.COLLECTION
        )
        self.assertEqual(account.balance, 30)
        self.assertEqual(account.total_earned, 30)
        self.assertEqual(account.total_spent, 0)

    def test_impact_scales_the_award(self):
        critical = make_contribution(self.student, impact=ContributionImpact.CRITICAL)
        services.approve_contribution(contribution=critical, actor=self.reviewer)

        # 30 base x 3.0 for critical
        self.assertEqual(
            PointAccount.objects.get(
                student=self.student, system=SystemScope.COLLECTION
            ).balance,
            90,
        )

    def test_a_reviewer_can_override_the_award(self):
        contribution = make_contribution(self.student)

        approved = services.approve_contribution(
            contribution=contribution, actor=self.reviewer, points=5
        )

        self.assertEqual(approved.points_awarded, 5)

    def test_points_are_scoped_to_the_system(self):
        services.approve_contribution(
            contribution=make_contribution(self.student, system=SystemScope.LOCKER),
            actor=self.reviewer,
        )

        self.assertEqual(
            PointAccount.objects.get(
                student=self.student, system=SystemScope.LOCKER
            ).balance,
            30,
        )
        self.assertFalse(
            PointAccount.objects.filter(
                student=self.student, system=SystemScope.COLLECTION
            ).exists()
        )

    def test_the_ledger_explains_the_balance(self):
        for _ in range(3):
            services.approve_contribution(
                contribution=make_contribution(self.student), actor=self.reviewer
            )

        account = PointAccount.objects.get(
            student=self.student, system=SystemScope.COLLECTION
        )
        entries = PointTransaction.objects.filter(account=account)

        self.assertEqual(entries.count(), 3)
        self.assertEqual(sum(entry.delta for entry in entries), account.balance)
        self.assertEqual(account.balance, account.total_earned - account.total_spent)
        self.assertEqual(entries.order_by("id").last().balance_after, account.balance)

    def test_approving_twice_is_refused(self):
        contribution = make_contribution(self.student)
        services.approve_contribution(contribution=contribution, actor=self.reviewer)

        with self.assertRaises(ValidationError):
            services.approve_contribution(
                contribution=contribution, actor=self.reviewer
            )

    def test_rejecting_needs_remarks(self):
        with self.assertRaises(ValidationError):
            services.reject_contribution(
                contribution=make_contribution(self.student),
                actor=self.reviewer,
                remarks="   ",
            )

    def test_revoking_takes_the_points_back(self):
        contribution = make_contribution(self.student)
        services.approve_contribution(contribution=contribution, actor=self.reviewer)

        services.revoke_contribution(
            contribution=contribution, actor=self.reviewer, remarks="Not reproducible."
        )

        account = PointAccount.objects.get(
            student=self.student, system=SystemScope.COLLECTION
        )
        self.assertEqual(account.balance, 0)
        self.assertEqual(account.total_earned, 30)
        self.assertEqual(account.total_spent, 30)

    def test_a_balance_can_never_go_negative(self):
        with self.assertRaises(ValidationError):
            services.apply_transaction(
                student=self.student,
                system=SystemScope.COLLECTION,
                delta=-10,
                reason=PointTransaction.Reason.ADJUSTMENT,
            )


class FeeDiscountTests(TestCase):
    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.student = make_student(self.program)
        self.reviewer = make_staff(self.institute)
        self.category = make_category(self.institute, fee="1000.00")
        self.fee = make_fee(self.student, self.category, amount="1000.00")

        PointPolicy.objects.update_or_create(
            institute=self.institute,
            system=SystemScope.COLLECTION,
            defaults={
                "peso_per_point": Decimal("1.00"),
                "min_points_per_redemption": 10,
                "max_points_per_redemption": 500,
                "max_discount_percent": Decimal("50.00"),
            },
        )
        services.apply_transaction(
            student=self.student,
            system=SystemScope.COLLECTION,
            delta=200,
            reason=PointTransaction.Reason.ADJUSTMENT,
            remarks="test fixture",
        )

    def _request(self, points=100):
        return services.request_discount(
            fee=self.fee,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=points,
            actor=self.student.user,
        )

    def test_requesting_reserves_the_points_immediately(self):
        discount = self._request(points=100)

        self.assertEqual(discount.status, FeeDiscount.Status.PENDING)
        self.assertEqual(discount.amount, Decimal("100.00"))
        self.assertEqual(
            PointAccount.objects.get(
                student=self.student, system=SystemScope.COLLECTION
            ).balance,
            100,
        )
        # The fee has not moved yet - approval does that.
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("1000.00"))

    def test_you_cannot_queue_more_than_you_hold(self):
        self._request(points=150)

        with self.assertRaises(ValidationError):
            self._request(points=100)

    def test_approving_moves_relief_and_balance_but_never_the_bill(self):
        """
        Reports derive `collected` as total - discount - balance, and it must
        not move: a discount lowers what is expected, it does not bring money
        in. `total_amount` stays put so the fee can still say what it billed.
        """
        discount = self._request(points=100)
        collected_before = (
            self.fee.total_amount - self.fee.discount_total - self.fee.balance
        )

        services.approve_discount(discount=discount, actor=self.reviewer)

        self.fee.refresh_from_db()
        self.assertEqual(self.fee.total_amount, Decimal("1000.00"))
        self.assertEqual(self.fee.discount_total, Decimal("100.00"))
        self.assertEqual(self.fee.balance, Decimal("900.00"))
        self.assertEqual(
            self.fee.total_amount - self.fee.discount_total - self.fee.balance,
            collected_before,
        )

    def test_the_fee_identity_holds_after_a_discount(self):
        """billed = collected + relief + outstanding, always."""
        discount = self._request(points=100)
        services.approve_discount(discount=discount, actor=self.reviewer)

        self.fee.refresh_from_db()
        collected = (
            self.fee.total_amount - self.fee.discount_total - self.fee.balance
        )
        self.assertEqual(
            collected + self.fee.discount_total + self.fee.balance,
            self.fee.total_amount,
        )

    def test_a_fee_discounted_to_zero_becomes_paid(self):
        PointPolicy.objects.filter(institute=self.institute).update(
            max_discount_percent=Decimal("100.00")
        )
        small = make_fee(self.student, self.category, amount="50.00")

        discount = services.request_discount(
            fee=small,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=50,
            actor=self.student.user,
        )
        services.approve_discount(discount=discount, actor=self.reviewer)

        small.refresh_from_db()
        self.assertEqual(small.balance, Decimal("0.00"))
        self.assertEqual(small.status, Fee.FeeStatus.PAID)

    def test_the_policy_caps_how_much_of_a_fee_points_may_cover(self):
        # 50% of 1000 is 500; 600 points would be 600 pesos.
        services.apply_transaction(
            student=self.student,
            system=SystemScope.COLLECTION,
            delta=600,
            reason=PointTransaction.Reason.ADJUSTMENT,
            remarks="top up",
        )

        with self.assertRaises(ValidationError):
            self._request(points=501)

    def test_below_the_minimum_is_refused(self):
        with self.assertRaises(ValidationError):
            self._request(points=5)

    def test_rejecting_refunds_the_points_and_leaves_the_fee_alone(self):
        discount = self._request(points=100)

        services.reject_discount(
            discount=discount, actor=self.reviewer, remarks="Already settled."
        )

        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("1000.00"))
        self.assertEqual(
            PointAccount.objects.get(
                student=self.student, system=SystemScope.COLLECTION
            ).balance,
            200,
        )

    def test_cancelling_restores_the_fee_and_the_points(self):
        discount = self._request(points=100)
        services.approve_discount(discount=discount, actor=self.reviewer)

        services.cancel_discount(
            discount=discount, actor=self.reviewer, reason="Applied to the wrong fee."
        )

        self.fee.refresh_from_db()
        self.assertEqual(self.fee.total_amount, Decimal("1000.00"))
        # The relief is given back, not merely offset.
        self.assertEqual(self.fee.discount_total, Decimal("0.00"))
        self.assertEqual(self.fee.balance, Decimal("1000.00"))
        self.assertEqual(self.fee.status, Fee.FeeStatus.PENDING)
        self.assertEqual(
            PointAccount.objects.get(
                student=self.student, system=SystemScope.COLLECTION
            ).balance,
            200,
        )

    def test_cancelling_needs_a_reason(self):
        discount = self._request(points=100)
        services.approve_discount(discount=discount, actor=self.reviewer)

        with self.assertRaises(ValidationError):
            services.cancel_discount(
                discount=discount, actor=self.reviewer, reason=""
            )

    def test_a_paid_fee_cannot_be_discounted(self):
        self.fee.status = Fee.FeeStatus.PAID
        self.fee.balance = Decimal("0.00")
        self.fee.save()

        with self.assertRaises(ValidationError):
            self._request(points=100)

    def test_points_from_one_system_cannot_pay_from_another(self):
        with self.assertRaises(ValidationError):
            services.request_discount(
                fee=self.fee,
                student=self.student,
                system=SystemScope.LOCKER,
                points=100,
                actor=self.student.user,
            )


class LeaderboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.reviewer = make_staff(cls.institute)

        cls.top = make_student(cls.program)
        cls.middle = make_student(cls.program)
        cls.quiet = make_student(cls.program)

        for _ in range(3):
            services.approve_contribution(
                contribution=make_contribution(cls.top), actor=cls.reviewer
            )
        services.approve_contribution(
            contribution=make_contribution(cls.middle), actor=cls.reviewer
        )

    def test_it_ranks_by_points_earned(self):
        rows = selectors.leaderboard(institute_id=self.institute.pk)

        self.assertEqual([row["student_id"] for row in rows], [self.top.pk, self.middle.pk])
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["points_earned"], 90)
        self.assertEqual(rows[0]["approved_contributions"], 3)

    def test_spending_points_does_not_cost_you_your_place(self):
        """Ranking on the balance would punish the students who used the scheme."""
        services.apply_transaction(
            student=self.top,
            system=SystemScope.COLLECTION,
            delta=-90,
            reason=PointTransaction.Reason.REDEMPTION,
        )

        rows = selectors.leaderboard(institute_id=self.institute.pk)

        self.assertEqual(rows[0]["student_id"], self.top.pk)
        self.assertEqual(rows[0]["points_earned"], 90)
        self.assertEqual(rows[0]["points_balance"], 0)

    def test_a_student_with_no_points_is_off_the_board(self):
        rows = selectors.leaderboard(institute_id=self.institute.pk)

        self.assertNotIn(self.quiet.pk, [row["student_id"] for row in rows])
        self.assertIsNone(
            selectors.student_rank(student=self.quiet, institute_id=self.institute.pk)
        )

    def test_the_board_can_be_read_per_system(self):
        services.approve_contribution(
            contribution=make_contribution(self.quiet, system=SystemScope.LOCKER),
            actor=self.reviewer,
        )

        locker = selectors.leaderboard(
            institute_id=self.institute.pk, system=SystemScope.LOCKER
        )

        self.assertEqual([row["student_id"] for row in locker], [self.quiet.pk])

    def test_another_institute_is_not_on_the_board(self):
        other_institute = make_institute()
        other = make_student(make_program(other_institute))
        services.approve_contribution(
            contribution=make_contribution(other),
            actor=make_staff(other_institute),
        )

        rows = selectors.leaderboard(institute_id=self.institute.pk)

        self.assertNotIn(other.pk, [row["student_id"] for row in rows])


class ContributionEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.student = make_student(self.program)
        self.other_student = make_student(self.program)
        self.admin = make_staff(self.institute)
        self.desk = make_staff(self.institute, role=Roles.CMS_STAFF)

    def _file_one(self):
        self.client.force_authenticate(self.student.user)
        return self.client.post(
            "/api/v1/contributions/",
            {
                "system": SystemScope.COLLECTION,
                "kind": ContributionKind.BUG_REPORT,
                "impact": ContributionImpact.MEDIUM,
                "title": "Fee list shows a stale balance",
                "description": "The list keeps the old balance until I sign out.",
            },
            format="json",
        )

    def test_a_student_can_file_one(self):
        response = self._file_one()

        self.assertEqual(response.status_code, 201)
        contribution = Contribution.objects.get()
        self.assertEqual(contribution.student, self.student)
        self.assertEqual(contribution.status, Contribution.Status.PENDING)

    def test_a_thin_description_is_refused(self):
        self.client.force_authenticate(self.student.user)

        response = self.client.post(
            "/api/v1/contributions/",
            {
                "system": SystemScope.COLLECTION,
                "kind": ContributionKind.OTHER,
                "title": "It is broken",
                "description": "broken",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_a_student_sees_only_their_own(self):
        make_contribution(self.student)
        make_contribution(self.other_student)

        self.client.force_authenticate(self.student.user)
        response = self.client.get("/api/v1/contributions/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["total_items"], 1)

    def test_staff_see_their_whole_institute(self):
        make_contribution(self.student)
        make_contribution(self.other_student)

        self.client.force_authenticate(self.desk)
        response = self.client.get("/api/v1/contributions/")

        self.assertEqual(response.json()["data"]["total_items"], 2)

    def test_a_student_cannot_approve(self):
        contribution = make_contribution(self.student)
        self.client.force_authenticate(self.student.user)

        response = self.client.post(
            f"/api/v1/contributions/{contribution.pk}/approve/", {}, format="json"
        )

        self.assertEqual(response.status_code, 403)

    def test_desk_staff_cannot_approve_either(self):
        """Reviewing is `review_contribution`, which the desk role does not hold."""
        contribution = make_contribution(self.student)
        self.client.force_authenticate(self.desk)

        response = self.client.post(
            f"/api/v1/contributions/{contribution.pk}/approve/", {}, format="json"
        )

        self.assertEqual(response.status_code, 403)

    def test_an_admin_can_approve_and_the_points_land(self):
        contribution = make_contribution(self.student)
        self.client.force_authenticate(self.admin)

        response = self.client.post(
            f"/api/v1/contributions/{contribution.pk}/approve/",
            {"remarks": "Reproduced."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["points_awarded"], 30)
        self.assertEqual(
            PointAccount.objects.get(
                student=self.student, system=SystemScope.COLLECTION
            ).balance,
            30,
        )

    def test_a_student_cannot_edit_a_reviewed_contribution(self):
        contribution = make_contribution(self.student)
        services.approve_contribution(contribution=contribution, actor=self.admin)

        self.client.force_authenticate(self.student.user)
        response = self.client.patch(
            f"/api/v1/contributions/{contribution.pk}/",
            {"title": "Rewriting history after the fact"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_the_wallet_endpoint_reports_the_students_own_points(self):
        services.approve_contribution(
            contribution=make_contribution(self.student), actor=self.admin
        )

        self.client.force_authenticate(self.student.user)
        response = self.client.get("/api/v1/contribution-points/me/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["total_balance"], 30)
        self.assertEqual(payload["rank"], 1)
        # Zero-filled, so the UI can render every system without branching.
        self.assertEqual(len(payload["wallets"]), len(SystemScope.choices))

    def test_the_leaderboard_is_visible_to_students(self):
        services.approve_contribution(
            contribution=make_contribution(self.student), actor=self.admin
        )

        self.client.force_authenticate(self.student.user)
        response = self.client.get("/api/v1/contribution-points/leaderboard/")

        self.assertEqual(response.status_code, 200)
        page = response.json()["data"]
        self.assertEqual(len(page["data"]), 1)
        # Paged like every other list, so the UI can keep scrolling.
        self.assertEqual(page["total_items"], 1)
        self.assertEqual(page["data"][0]["rank"], 1)

    def test_the_board_pages_and_the_ranks_stay_absolute(self):
        """Page two starts at 3rd, not 1st.

        Renumbering per page would hand out the same medal on every screen.
        """
        for count in (5, 4, 3, 2):
            student = make_student(self.program)
            for _ in range(count):
                services.approve_contribution(
                    contribution=make_contribution(student), actor=self.admin
                )

        self.client.force_authenticate(self.admin)
        url = "/api/v1/contribution-points/leaderboard/?per_page=2"

        first = self.client.get(url).json()["data"]
        second = self.client.get(url + "&current_page=2").json()["data"]

        self.assertEqual(first["total_items"], 4)
        self.assertEqual(first["total_pages"], 2)
        self.assertEqual([row["rank"] for row in first["data"]], [1, 2])
        self.assertEqual([row["rank"] for row in second["data"]], [3, 4])
        # Descending on points earned, still, across the page boundary.
        self.assertEqual(
            [row["points_earned"] for row in first["data"] + second["data"]],
            sorted(
                (row["points_earned"] for row in first["data"] + second["data"]),
                reverse=True,
            ),
        )

    def test_the_board_defaults_to_ten_places(self):
        """The default page is small: nobody reads past the first screen."""
        for index in range(12):
            student = make_student(self.program)
            for _ in range(index + 1):
                services.approve_contribution(
                    contribution=make_contribution(student), actor=self.admin
                )

        self.client.force_authenticate(self.admin)
        page = self.client.get(
            "/api/v1/contribution-points/leaderboard/"
        ).json()["data"]

        self.assertEqual(len(page["data"]), 10)
        self.assertEqual(page["total_items"], 12)

    def test_searching_the_board_keeps_the_place_absolute(self):
        """Looking yourself up asks "where am I", not "put me first".

        A filtered page numbered by its own position would answer the
        opposite question, and answer it wrongly.
        """
        wanted = None
        for index, count in enumerate((9, 7, 5, 3, 1)):
            student = make_student(self.program)
            for _ in range(count):
                services.approve_contribution(
                    contribution=make_contribution(student), actor=self.admin
                )
            if index == 3:  # fourth place
                wanted = student

        self.client.force_authenticate(self.admin)
        page = self.client.get(
            f"/api/v1/contribution-points/leaderboard/"
            f"?search={wanted.s_studentID}"
        ).json()["data"]

        self.assertEqual(page["total_items"], 1)
        self.assertEqual(page["data"][0]["student_id"], wanted.pk)
        self.assertEqual(page["data"][0]["rank"], 4)

    def test_a_search_that_matches_nobody_is_an_empty_board(self):
        services.approve_contribution(
            contribution=make_contribution(self.student), actor=self.admin
        )

        self.client.force_authenticate(self.admin)
        page = self.client.get(
            "/api/v1/contribution-points/leaderboard/?search=nobody-by-that-name"
        ).json()["data"]

        self.assertEqual(page["data"], [])
        self.assertEqual(page["total_items"], 0)

    def test_students_on_the_same_points_share_a_place(self):
        """Two people tied for first are both first, not first and second."""
        tied = [make_student(self.program), make_student(self.program)]
        for student in tied:
            for _ in range(4):
                services.approve_contribution(
                    contribution=make_contribution(student), actor=self.admin
                )

        self.client.force_authenticate(self.admin)
        ranks = [
            self.client.get(
                f"/api/v1/contribution-points/leaderboard/?search={s.s_studentID}"
            ).json()["data"]["data"][0]["rank"]
            for s in tied
        ]

        self.assertEqual(ranks, [1, 1])


class FeeDiscountEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.student = make_student(self.program)
        self.admin = make_staff(self.institute)
        self.category = make_category(self.institute, fee="500.00")
        self.fee = make_fee(self.student, self.category, amount="500.00")

        services.get_policy(
            institute_id=self.institute.pk, system=SystemScope.COLLECTION
        )
        services.apply_transaction(
            student=self.student,
            system=SystemScope.COLLECTION,
            delta=100,
            reason=PointTransaction.Reason.ADJUSTMENT,
            remarks="fixture",
        )

    def test_a_student_can_quote_before_committing(self):
        self.client.force_authenticate(self.student.user)

        response = self.client.post(
            "/api/v1/fee-discounts/quote/",
            {"fee": self.fee.pk, "system": SystemScope.COLLECTION, "points": 50},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["available_points"], 100)
        self.assertEqual(Decimal(payload["amount"]), Decimal("50.00"))
        # Nothing was reserved.
        self.assertFalse(FeeDiscount.objects.exists())

    def test_a_student_can_request_and_an_admin_approves(self):
        self.client.force_authenticate(self.student.user)
        created = self.client.post(
            "/api/v1/fee-discounts/request/",
            {"fee": self.fee.pk, "system": SystemScope.COLLECTION, "points": 50},
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        discount_id = created.json()["data"]["id"]

        self.client.force_authenticate(self.admin)
        approved = self.client.post(
            f"/api/v1/fee-discounts/{discount_id}/approve/", {}, format="json"
        )

        self.assertEqual(approved.status_code, 200)
        self.fee.refresh_from_db()
        self.assertEqual(self.fee.balance, Decimal("450.00"))
        # Gross is preserved; the relief lives in its own column.
        self.assertEqual(self.fee.total_amount, Decimal("500.00"))
        self.assertEqual(self.fee.discount_total, Decimal("50.00"))

    def test_a_student_cannot_approve_their_own_request(self):
        discount = services.request_discount(
            fee=self.fee,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=50,
            actor=self.student.user,
        )

        self.client.force_authenticate(self.student.user)
        response = self.client.post(
            f"/api/v1/fee-discounts/{discount.pk}/approve/", {}, format="json"
        )

        self.assertEqual(response.status_code, 403)

    def test_a_student_cannot_discount_another_students_fee(self):
        other = make_student(self.program)
        other_fee = make_fee(other, self.category, amount="500.00")

        self.client.force_authenticate(self.student.user)
        response = self.client.post(
            "/api/v1/fee-discounts/request/",
            {"fee": other_fee.pk, "system": SystemScope.COLLECTION, "points": 50},
            format="json",
        )

        # `_fee_in_scope` narrows to the caller's own fees, so it is not found
        # at all rather than found-and-refused.
        self.assertEqual(response.status_code, 404)

    def test_the_history_endpoint_lists_every_attempt(self):
        first = services.request_discount(
            fee=self.fee,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=50,
            actor=self.student.user,
        )
        services.reject_discount(
            discount=first, actor=self.admin, remarks="Settled at the counter."
        )
        services.request_discount(
            fee=self.fee,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=50,
            actor=self.student.user,
        )

        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/v1/fee-discounts/for-fee/?fee={self.fee.pk}")

        self.assertEqual(response.status_code, 200)
        page = response.json()["data"]
        self.assertEqual(page["total_items"], 2)
        self.assertEqual(
            {row["status"] for row in page["data"]}, {"pending", "rejected"}
        )


class PointPolicyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.admin = make_staff(self.institute, role=Roles.SCHOOL_ADMIN)
        self.student = make_student(self.program)

    def test_a_policy_is_created_on_demand_with_sane_defaults(self):
        policy = services.get_policy(
            institute_id=self.institute.pk, system=SystemScope.VOTING
        )

        self.assertTrue(policy.is_active)
        self.assertEqual(policy.award_for(ContributionKind.BUG_REPORT, "medium"), 30)

    def test_an_inactive_policy_blocks_awards(self):
        policy = services.get_policy(
            institute_id=self.institute.pk, system=SystemScope.COLLECTION
        )
        policy.is_active = False
        policy.save()

        with self.assertRaises(ValidationError):
            services.approve_contribution(
                contribution=make_contribution(self.student),
                actor=make_staff(self.institute),
            )

    def test_an_admin_can_retune_the_awards(self):
        policy = services.get_policy(
            institute_id=self.institute.pk, system=SystemScope.COLLECTION
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            f"/api/v1/point-policies/{policy.pk}/",
            {"awards": {"bug_report": 100}, "peso_per_point": "2.50"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        policy.refresh_from_db()
        self.assertEqual(policy.awards["bug_report"], 100)
        self.assertEqual(policy.peso_value(10), Decimal("25.00"))

    def test_an_unknown_kind_in_the_awards_map_is_rejected(self):
        policy = services.get_policy(
            institute_id=self.institute.pk, system=SystemScope.COLLECTION
        )
        self.client.force_authenticate(self.admin)

        response = self.client.patch(
            f"/api/v1/point-policies/{policy.pk}/",
            {"awards": {"telepathy": 100}},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_a_student_cannot_change_a_policy(self):
        policy = services.get_policy(
            institute_id=self.institute.pk, system=SystemScope.COLLECTION
        )
        self.client.force_authenticate(self.student.user)

        response = self.client.patch(
            f"/api/v1/point-policies/{policy.pk}/",
            {"peso_per_point": "1000.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)


class FeeDiscountHistoryOnFeeTests(TestCase):
    """`GET /fees/{id}/discounts/` - the history where the fee screen needs it."""

    def setUp(self):
        self.client = APIClient()
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.student = make_student(self.program)
        self.admin = make_staff(self.institute)
        self.category = make_category(self.institute, fee="500.00")
        self.fee = make_fee(self.student, self.category, amount="500.00")

        services.get_policy(
            institute_id=self.institute.pk, system=SystemScope.COLLECTION
        )
        services.apply_transaction(
            student=self.student,
            system=SystemScope.COLLECTION,
            delta=100,
            reason=PointTransaction.Reason.ADJUSTMENT,
            remarks="fixture",
        )
        discount = services.request_discount(
            fee=self.fee,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=50,
            actor=self.student.user,
        )
        services.approve_discount(discount=discount, actor=self.admin)

    def test_the_student_sees_their_own_fees_history(self):
        self.client.force_authenticate(self.student.user)

        response = self.client.get(f"/api/v1/fees/{self.fee.pk}/discounts/")

        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["points_spent"], 50)
        self.assertEqual(Decimal(rows[0]["previous_balance"]), Decimal("500.00"))

    def test_another_students_fee_is_out_of_reach(self):
        other = make_student(self.program)
        self.client.force_authenticate(other.user)

        response = self.client.get(f"/api/v1/fees/{self.fee.pk}/discounts/")

        self.assertEqual(response.status_code, 404)


ALL_ON = {"contributions": True, "contribution_points": True, "complaints": True}
POINTS_OFF = {**ALL_ON, "contribution_points": False}
CONTRIBUTIONS_OFF = {**ALL_ON, "contributions": False}


class FeatureFlagTests(TestCase):
    """What an institute that does not run the scheme actually gets."""

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.student = make_student(cls.program)
        cls.reviewer = make_staff(cls.institute)

    def setUp(self):
        self.client = APIClient()

    @override_settings(FEATURES=POINTS_OFF)
    def test_approving_still_works_but_pays_nothing(self):
        """Trialling the scheme: reports are useful before awards are."""
        contribution = make_contribution(self.student)

        approved = services.approve_contribution(
            contribution=contribution, actor=self.reviewer
        )

        self.assertEqual(approved.status, Contribution.Status.APPROVED)
        self.assertEqual(approved.points_awarded, 0)
        self.assertFalse(PointAccount.objects.filter(student=self.student).exists())
        self.assertFalse(PointTransaction.objects.exists())

    @override_settings(FEATURES=POINTS_OFF)
    def test_a_reviewers_override_cannot_reintroduce_points(self):
        contribution = make_contribution(self.student)

        approved = services.approve_contribution(
            contribution=contribution, actor=self.reviewer, points=500
        )

        self.assertEqual(approved.points_awarded, 0)
        self.assertFalse(PointTransaction.objects.exists())

    @override_settings(FEATURES=POINTS_OFF)
    def test_the_ranking_is_refused_when_awards_are_off(self):
        self.client.force_authenticate(self.reviewer)

        response = self.client.get("/api/v1/contribution-points/leaderboard/")

        self.assertEqual(response.status_code, 403)

    @override_settings(FEATURES=POINTS_OFF)
    def test_a_fees_discount_panel_is_refused_when_awards_are_off(self):
        fee = make_fee(self.student, make_category(self.institute))
        self.client.force_authenticate(self.reviewer)

        response = self.client.get(f"/api/v1/fees/{fee.pk}/discounts/")

        self.assertEqual(response.status_code, 403)
        self.assertIn("not enabled", response.data["detail"].lower())

    @override_settings(FEATURES=ALL_ON)
    def test_the_same_two_routes_answer_when_the_feature_is_on(self):
        fee = make_fee(self.student, make_category(self.institute))
        self.client.force_authenticate(self.reviewer)

        self.assertEqual(
            self.client.get("/api/v1/contribution-points/leaderboard/").status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/api/v1/fees/{fee.pk}/discounts/").status_code, 200
        )

    @override_settings(FEATURES=CONTRIBUTIONS_OFF)
    def test_filing_a_report_is_refused_when_the_module_is_off(self):
        self.client.force_authenticate(self.student.user)

        response = self.client.post(
            "/api/v1/contributions/",
            {
                "system": SystemScope.COLLECTION,
                "kind": ContributionKind.BUG_REPORT,
                "impact": ContributionImpact.LOW,
                "title": "Anything at all",
                "description": "Should not be accepted here.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Contribution.objects.exists())

    def test_the_routes_are_not_even_registered_when_the_module_is_off(self):
        """404, not 403: the endpoints are genuinely absent."""
        import importlib

        import api.urls

        with override_settings(FEATURES=CONTRIBUTIONS_OFF):
            try:
                reloaded = importlib.reload(api.urls)
                registered = {
                    prefix for prefix, _viewset, _basename in reloaded.router.registry
                }
            finally:
                # Other tests reverse against the real table.
                importlib.reload(api.urls)

        self.assertNotIn("contributions", registered)
        self.assertNotIn("contribution-points", registered)
        self.assertNotIn("fee-discounts", registered)
        self.assertIn("fees", registered)


@override_settings(FEATURES=ALL_ON)
class DiscountCapRefundTests(TestCase):
    """
    The cap must not silently pocket the points it did not spend.

    Points leave the wallet when the request is made, priced against the
    balance at that moment. A coupon or a payment landing before approval
    shrinks the fee, and `approve_discount` caps the amount to what is left -
    correctly. What was missing is the other half: the points that bought
    nothing have to come back, because nothing in the ledger would otherwise
    show the student had paid for relief they never received.
    """

    def setUp(self):
        self.institute = make_institute()
        self.program = make_program(self.institute)
        self.admin = make_staff(self.institute)
        self.student = make_student(self.program)
        self.category = make_category(self.institute)

        self.policy = services.get_policy(
            institute_id=self.institute.pk, system=SystemScope.COLLECTION
        )
        self.policy.peso_per_point = Decimal("10.00")
        self.policy.min_points_per_redemption = 1
        self.policy.max_points_per_redemption = 100
        self.policy.max_discount_percent = Decimal("100.00")
        self.policy.save()

        services.apply_transaction(
            student=self.student,
            system=SystemScope.COLLECTION,
            delta=100,
            reason=PointTransaction.Reason.ADJUSTMENT,
            remarks="seed",
        )

    def balance(self):
        return services.get_account(
            student=self.student, system=SystemScope.COLLECTION
        ).balance

    def test_points_the_cap_did_not_buy_are_returned(self):
        fee = make_fee(self.student, self.category, amount="500.00")

        # 50 points = PHP 500, the whole fee.
        discount = services.request_discount(
            fee=fee,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=50,
            actor=self.student.user,
        )
        self.assertEqual(self.balance(), 50)

        # Somebody pays PHP 300 of it before the reviewer gets there.
        fee.balance = Decimal("200.00")
        fee.save(update_fields=["balance"])

        approved = services.approve_discount(discount=discount, actor=self.admin)

        self.assertEqual(approved.amount, Decimal("200.00"))
        # PHP 200 at PHP 10 a point is 20 points; the other 30 come back.
        self.assertEqual(approved.points_spent, 20)
        self.assertEqual(self.balance(), 80)

    def test_the_refund_is_its_own_ledger_line(self):
        """A balance that changed with nothing explaining it is unauditable."""
        fee = make_fee(self.student, self.category, amount="500.00")
        discount = services.request_discount(
            fee=fee,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=50,
            actor=self.student.user,
        )
        fee.balance = Decimal("200.00")
        fee.save(update_fields=["balance"])

        services.approve_discount(discount=discount, actor=self.admin)

        entry = PointTransaction.objects.filter(
            reason=PointTransaction.Reason.REDEMPTION_ADJUSTED
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.delta, 30)
        self.assertIn("less owing", entry.remarks)
        # Not "cancelled": the student did receive a discount.
        self.assertNotIn("cancel", entry.get_reason_display().lower())

    def test_an_uncapped_approval_refunds_nothing(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        discount = services.request_discount(
            fee=fee,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=20,
            actor=self.student.user,
        )

        approved = services.approve_discount(discount=discount, actor=self.admin)

        self.assertEqual(approved.points_spent, 20)
        self.assertEqual(self.balance(), 80)
        self.assertFalse(
            PointTransaction.objects.filter(
                reason=PointTransaction.Reason.REDEMPTION_ADJUSTED
            ).exists()
        )

    def test_the_points_kept_always_cover_the_relief_given(self):
        """Rounding must never hand back a point that paid for something."""
        self.policy.peso_per_point = Decimal("30.00")
        self.policy.save()

        fee = make_fee(self.student, self.category, amount="300.00")
        discount = services.request_discount(
            fee=fee,
            student=self.student,
            system=SystemScope.COLLECTION,
            points=10,
            actor=self.student.user,
        )
        # PHP 55 left: 1.83 points' worth, so 2 points must be kept.
        fee.balance = Decimal("55.00")
        fee.save(update_fields=["balance"])

        approved = services.approve_discount(discount=discount, actor=self.admin)

        self.assertEqual(approved.amount, Decimal("55.00"))
        self.assertEqual(approved.points_spent, 2)
        self.assertGreaterEqual(
            approved.points_spent * self.policy.peso_per_point, approved.amount
        )
