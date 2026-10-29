"""
Coupons: the refusals, the arithmetic, and taking one back.

The happy path is one test. The rest of this file is about the two things
that go wrong with a bearer instrument — the wrong person using it, and
somebody needing to recall it after the money has already moved.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from api.coupons import services
from api.coupons.models import Coupon, CouponBatch, CouponClaim, CouponRedemption
from api.fees.models import Fee
from api.fees.tests.factories import (
    make_category,
    make_fee,
    make_institute,
    make_program,
    make_staff,
    make_student,
)

COUPONS_ON = {"contributions": False, "contribution_points": False,
              "complaints": False, "coupons": True}


def make_batch(institute, **kwargs):
    defaults = dict(
        name="IC Week 2026",
        value_type=CouponBatch.ValueType.FIXED,
        value=Decimal("50.00"),
        valid_from=timezone.now() - timedelta(days=1),
        expires_at=timezone.now() + timedelta(days=30),
    )
    defaults.update(kwargs)
    return CouponBatch.objects.create(institute=institute, **defaults)


@override_settings(FEATURES=COUPONS_ON)
class RedemptionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.category = make_category(cls.institute, name="Attendance Fine")
        cls.other_category = make_category(cls.institute, name="Tuition")
        cls.student = make_student(cls.program)
        cls.other_student = make_student(cls.program)

    def coupon(self, batch=None, student=None, **kwargs):
        batch = batch or make_batch(self.institute)
        return services.issue_batch(
            batch=batch, students=[student] if student else None, quantity=1, **kwargs
        )[0]

    # -- the happy path -----------------------------------------------------

    def test_applying_moves_relief_not_the_bill(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        coupon = self.coupon(student=self.student)

        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)

        fee.refresh_from_db()
        self.assertEqual(fee.total_amount, Decimal("500.00"))
        self.assertEqual(fee.discount_total, Decimal("50.00"))
        self.assertEqual(fee.balance, Decimal("450.00"))
        # collected must not have moved: nobody paid anything.
        self.assertEqual(
            fee.total_amount - fee.discount_total - fee.balance, Decimal("0.00")
        )

    def test_a_code_is_accepted_however_it_was_typed(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        coupon = self.coupon(student=self.student)

        services.apply(
            raw_code=f"  {coupon.code.lower()}  ",
            fee_id=fee.pk,
            student=self.student,
        )
        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("50.00"))

    def test_a_coupon_clearing_the_fee_marks_it_paid(self):
        fee = make_fee(self.student, self.category, amount="50.00")
        coupon = self.coupon(student=self.student)

        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)

        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("0.00"))
        self.assertEqual(fee.status, Fee.FeeStatus.PAID)

    def test_relief_never_exceeds_what_is_owed(self):
        """A 500 coupon on a 100 fee takes 100, not 500."""
        fee = make_fee(self.student, self.category, amount="100.00")
        batch = make_batch(self.institute, value=Decimal("500.00"))
        coupon = self.coupon(batch=batch, student=self.student)

        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)

        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("100.00"))
        self.assertEqual(fee.balance, Decimal("0.00"))

    # -- percentages --------------------------------------------------------

    def test_a_percentage_is_of_the_remaining_balance(self):
        """
        Not of the original bill. A student who already paid half a 500 fine
        and uses "50% off" gets 125 off what they still owe, not 250 off a
        debt of 250 - which would clear it outright.
        """
        fee = make_fee(self.student, self.category, amount="500.00")
        fee.balance = Decimal("250.00")
        fee.save(update_fields=["balance"])

        batch = make_batch(
            self.institute,
            value_type=CouponBatch.ValueType.PERCENT,
            value=Decimal("50"),
        )
        coupon = self.coupon(batch=batch, student=self.student)

        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)

        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("125.00"))
        self.assertEqual(fee.balance, Decimal("125.00"))

    def test_a_percentage_respects_its_ceiling(self):
        fee = make_fee(self.student, self.category, amount="1000.00")
        batch = make_batch(
            self.institute,
            value_type=CouponBatch.ValueType.PERCENT,
            value=Decimal("50"),
            max_discount=Decimal("100.00"),
        )
        coupon = self.coupon(batch=batch, student=self.student)

        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)

        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("100.00"))

    # -- the constraints ----------------------------------------------------

    def assertRefused(self, code, fn, *args, **kwargs):
        with self.assertRaises(services.CouponError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_a_bound_coupon_is_worthless_to_anyone_else(self):
        fee = make_fee(self.other_student, self.category, amount="500.00")
        coupon = self.coupon(student=self.student)

        self.assertRefused(
            "wrong_student",
            services.apply,
            raw_code=coupon.code,
            fee_id=fee.pk,
            student=self.other_student,
        )

    def test_the_category_allow_list_is_enforced_and_says_which(self):
        fee = make_fee(self.student, self.other_category, amount="500.00")
        batch = make_batch(self.institute)
        batch.categories.set([self.category])
        coupon = self.coupon(batch=batch, student=self.student)

        error = self.assertRefused(
            "category_not_allowed",
            services.apply,
            raw_code=coupon.code,
            fee_id=fee.pk,
            student=self.student,
        )
        # The message has to name the category, or it just sends them to the desk.
        self.assertIn("Attendance Fine", error.message)

    def test_an_empty_allow_list_means_any_category(self):
        fee = make_fee(self.student, self.other_category, amount="500.00")
        coupon = self.coupon(student=self.student)

        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)
        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("50.00"))

    def test_an_expired_coupon_is_refused(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        batch = make_batch(
            self.institute,
            valid_from=timezone.now() - timedelta(days=10),
            expires_at=timezone.now() - timedelta(days=1),
        )
        coupon = self.coupon(batch=batch, student=self.student)

        self.assertRefused(
            "expired", services.apply,
            raw_code=coupon.code, fee_id=fee.pk, student=self.student,
        )

    def test_a_coupon_before_its_window_is_refused(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        batch = make_batch(
            self.institute,
            valid_from=timezone.now() + timedelta(days=5),
            expires_at=timezone.now() + timedelta(days=10),
        )
        coupon = self.coupon(batch=batch, student=self.student)

        self.assertRefused(
            "not_yet_valid", services.apply,
            raw_code=coupon.code, fee_id=fee.pk, student=self.student,
        )

    def test_a_settled_fee_cannot_be_discounted(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        fee.status = Fee.FeeStatus.PAID
        fee.balance = Decimal("0.00")
        fee.save(update_fields=["status", "balance"])
        coupon = self.coupon(student=self.student)

        self.assertRefused(
            "fee_settled", services.apply,
            raw_code=coupon.code, fee_id=fee.pk, student=self.student,
        )

    def test_the_minimum_balance_floor_is_enforced(self):
        fee = make_fee(self.student, self.category, amount="20.00")
        batch = make_batch(self.institute, min_fee_balance=Decimal("100.00"))
        coupon = self.coupon(batch=batch, student=self.student)

        self.assertRefused(
            "below_minimum", services.apply,
            raw_code=coupon.code, fee_id=fee.pk, student=self.student,
        )

    def test_the_term_scope_is_enforced(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        batch = make_batch(self.institute, semester="2nd")
        coupon = self.coupon(batch=batch, student=self.student)

        if fee.semester != "2nd":
            self.assertRefused(
                "wrong_term", services.apply,
                raw_code=coupon.code, fee_id=fee.pk, student=self.student,
            )

    def test_only_one_coupon_per_fee(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        first = self.coupon(student=self.student)
        second = self.coupon(student=self.student)

        services.apply(raw_code=first.code, fee_id=fee.pk, student=self.student)
        self.assertRefused(
            "fee_has_coupon", services.apply,
            raw_code=second.code, fee_id=fee.pk, student=self.student,
        )

    def test_a_coupon_is_spent_once(self):
        first = make_fee(self.student, self.category, amount="500.00")
        second = make_fee(self.student, self.category, amount="500.00")
        coupon = self.coupon(student=self.student)

        services.apply(raw_code=coupon.code, fee_id=first.pk, student=self.student)
        self.assertRefused(
            "fully_redeemed", services.apply,
            raw_code=coupon.code, fee_id=second.pk, student=self.student,
        )

    def test_an_unknown_code_is_refused(self):
        self.assertRefused("not_found", services.find_coupon, "NOPE-NOPE-NOPE")

    # -- stacking with points ----------------------------------------------

    def test_a_coupon_stacks_on_top_of_an_existing_discount(self):
        """
        Points and coupons are independent, and both land in the same column.
        The cap is what stops the pair taking a fee below zero.
        """
        fee = make_fee(self.student, self.category, amount="500.00")
        # Stand in for an approved points discount.
        fee.discount_total = Decimal("400.00")
        fee.balance = Decimal("100.00")
        fee.save(update_fields=["discount_total", "balance"])

        batch = make_batch(self.institute, value=Decimal("300.00"))
        coupon = self.coupon(batch=batch, student=self.student)
        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)

        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("500.00"))
        self.assertEqual(fee.balance, Decimal("0.00"))
        # Never below zero, whatever the two add up to.
        self.assertGreaterEqual(fee.balance, Decimal("0.00"))
        self.assertLessEqual(fee.discount_total, fee.total_amount)


@override_settings(FEATURES=COUPONS_ON)
class RevocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.category = make_category(cls.institute, name="Org Dues")
        cls.student = make_student(cls.program)

    def test_voiding_an_unspent_coupon_moves_no_money(self):
        batch = make_batch(self.institute)
        coupon = services.issue_batch(batch=batch, quantity=1)[0]

        services.void_coupon(coupon=coupon, actor=self.staff, reason="Wrong value")

        coupon.refresh_from_db()
        self.assertEqual(coupon.status, Coupon.Status.REVOKED)

    def test_voiding_a_spent_coupon_is_refused(self):
        """Voiding would leave the discount in place. Reversal is the verb."""
        fee = make_fee(self.student, self.category, amount="500.00")
        batch = make_batch(self.institute)
        coupon = services.issue_batch(batch=batch, students=[self.student])[0]
        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)

        with self.assertRaises(services.CouponError) as caught:
            services.void_coupon(coupon=coupon, actor=self.staff, reason="Oops")
        self.assertEqual(caught.exception.code, "already_redeemed")

    def test_revoking_a_batch_voids_the_unspent_and_leaves_the_spent(self):
        """
        The important one. A loop that reversed everything would re-bill
        students who did nothing wrong.
        """
        fee = make_fee(self.student, self.category, amount="500.00")
        batch = make_batch(self.institute)
        spent, *unspent = services.issue_batch(
            batch=batch, quantity=4, max_redemptions=1
        )
        services.apply(raw_code=spent.code, fee_id=fee.pk, student=self.student)

        result = services.revoke_batch(
            batch=batch, actor=self.staff, reason="Event cancelled"
        )

        self.assertEqual(result["voided"], 3)
        self.assertEqual(result["already_redeemed"], 1)

        # The spent one keeps its discount: the student was not re-billed.
        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("50.00"))
        self.assertEqual(fee.balance, Decimal("450.00"))

        for coupon in unspent:
            coupon.refresh_from_db()
            self.assertEqual(coupon.status, Coupon.Status.REVOKED)

    def test_reversing_an_unpaid_fee_restores_the_balance(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        batch = make_batch(self.institute)
        coupon = services.issue_batch(batch=batch, students=[self.student])[0]
        redemption = services.apply(
            raw_code=coupon.code, fee_id=fee.pk, student=self.student
        )

        services.reverse_redemption(
            redemption=redemption, actor=self.staff, reason="Issued in error"
        )

        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("0.00"))
        self.assertEqual(fee.balance, Decimal("500.00"))
        self.assertEqual(fee.total_amount, Decimal("500.00"))

        redemption.refresh_from_db()
        self.assertEqual(redemption.status, CouponRedemption.Status.REVERSED)

        # The coupon becomes usable again rather than being burned.
        coupon.refresh_from_db()
        self.assertEqual(coupon.uses_taken, 0)
        self.assertEqual(coupon.status, Coupon.Status.ACTIVE)

    def test_reversing_a_settled_fee_needs_a_deliberate_confirmation(self):
        """
        The sharp edge. Putting the amount back creates a debt the student
        has already been told they do not owe, and unlike points nothing is
        refunded to make them whole.
        """
        fee = make_fee(self.student, self.category, amount="50.00")
        batch = make_batch(self.institute)
        coupon = services.issue_batch(batch=batch, students=[self.student])[0]
        redemption = services.apply(
            raw_code=coupon.code, fee_id=fee.pk, student=self.student
        )

        fee.refresh_from_db()
        self.assertEqual(fee.status, Fee.FeeStatus.PAID)

        with self.assertRaises(services.CouponError) as caught:
            services.reverse_redemption(
                redemption=redemption, actor=self.staff, reason="Wrong student"
            )
        self.assertEqual(caught.exception.code, "fee_already_paid")

        # Nothing moved on the refusal.
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("0.00"))

        services.reverse_redemption(
            redemption=redemption,
            actor=self.staff,
            reason="Wrong student",
            allow_paid=True,
        )
        fee.refresh_from_db()
        self.assertEqual(fee.balance, Decimal("50.00"))
        self.assertEqual(fee.status, Fee.FeeStatus.PENDING)

    def test_a_reason_is_always_required(self):
        batch = make_batch(self.institute)
        coupon = services.issue_batch(batch=batch, quantity=1)[0]

        with self.assertRaises(services.CouponError):
            services.void_coupon(coupon=coupon, actor=self.staff, reason="  ")
        with self.assertRaises(services.CouponError):
            services.revoke_batch(batch=batch, actor=self.staff, reason="")


@override_settings(FEATURES=COUPONS_ON)
class ClaimTests(TestCase):
    """Shared codes, and the one-claim-per-student rule."""

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.other_program = make_program(cls.institute)
        cls.category = make_category(cls.institute, name="Dues")
        cls.student = make_student(cls.program)
        cls.other_student = make_student(cls.program)

    def shared(self, **kwargs):
        batch = make_batch(self.institute, kind=CouponBatch.Kind.QR_CAMPAIGN, **kwargs)
        return services.issue_batch(batch=batch, quantity=1, max_redemptions=10)[0]

    def test_two_students_can_each_claim_a_shared_code(self):
        coupon = self.shared()
        services.claim(raw_code=coupon.code, student=self.student)
        services.claim(raw_code=coupon.code, student=self.other_student)
        self.assertEqual(coupon.claims.count(), 2)

    def test_claiming_twice_returns_the_same_hold(self):
        coupon = self.shared()
        first = services.claim(raw_code=coupon.code, student=self.student)
        second = services.claim(raw_code=coupon.code, student=self.student)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(coupon.claims.count(), 1)

    def test_a_campaign_can_be_limited_to_one_year_level(self):
        coupon = self.shared(target_year_level=self.student.s_lvl + 1)

        with self.assertRaises(services.CouponError) as caught:
            services.claim(raw_code=coupon.code, student=self.student)
        self.assertEqual(caught.exception.code, "not_eligible")

    def test_a_claim_can_be_spent_later(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        coupon = self.shared()

        services.claim(raw_code=coupon.code, student=self.student)
        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)

        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("50.00"))
        self.assertEqual(
            CouponRedemption.objects.filter(
                student=self.student, status=CouponRedemption.Status.APPLIED
            ).count(),
            1,
        )


@override_settings(FEATURES=COUPONS_ON)
class ExpirySweepTests(TestCase):
    def test_the_sweep_expires_past_window_coupons(self):
        institute = make_institute()
        live = make_batch(institute)
        dead = make_batch(
            institute,
            valid_from=timezone.now() - timedelta(days=10),
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        services.issue_batch(batch=live, quantity=2)
        services.issue_batch(batch=dead, quantity=3)

        self.assertEqual(services.expire_due_coupons(), 3)
        self.assertEqual(
            Coupon.objects.filter(status=Coupon.Status.ACTIVE).count(), 2
        )


@override_settings(FEATURES=COUPONS_ON)
class ExportTests(TestCase):
    """
    Both formats actually render. A print run that fails at the moment
    somebody needs it is the worst time to find out.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.category = make_category(cls.institute, name="Attendance Fine")
        cls.students = [make_student(cls.program) for _ in range(3)]

    def batch_with_coupons(self, **kwargs):
        batch = make_batch(self.institute, reason="Quiz bee, 2nd place", **kwargs)
        batch.categories.set([self.category])
        services.issue_batch(batch=batch, students=self.students)
        batch.refresh_from_db()
        return batch

    def test_the_spreadsheet_carries_every_code(self):
        from openpyxl import load_workbook
        import io as _io

        from api.coupons import exports

        batch = self.batch_with_coupons()
        content = exports.to_xlsx(batch)

        sheet = load_workbook(_io.BytesIO(content)).active
        text = "\n".join(
            str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value
        )
        for coupon in batch.coupons.all():
            self.assertIn(coupon.code, text)
        # The terms a holder needs, not just the code.
        self.assertIn("₱50.00 off", text)
        self.assertIn("Attendance Fine", text)
        self.assertIn("Quiz bee, 2nd place", text)

    def test_the_printable_sheet_renders(self):
        from api.coupons import exports

        batch = self.batch_with_coupons()
        content = exports.to_pdf(batch)

        self.assertTrue(content.startswith(b"%PDF"))
        self.assertGreater(len(content), 1000)

    def test_an_odd_number_of_coupons_still_renders(self):
        """The two-column table has to close its last row."""
        from api.coupons import exports

        batch = make_batch(self.institute)
        services.issue_batch(batch=batch, quantity=5)
        self.assertTrue(exports.to_pdf(batch).startswith(b"%PDF"))


@override_settings(FEATURES=COUPONS_ON)
class AccessScopeTests(TestCase):
    """
    Who can see whose coupons.

    Students hold `coupons.view_coupon` so they can see their own. An
    institute-wide filter would have let any of them list every code in the
    institute — other people's names, and for a bearer coupon the code
    itself, which is the entire coupon.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.mine = make_student(cls.program)
        cls.theirs = make_student(cls.program)

        batch = make_batch(cls.institute)
        cls.my_coupon = services.issue_batch(batch=batch, students=[cls.mine])[0]
        cls.their_coupon = services.issue_batch(
            batch=batch, students=[cls.theirs]
        )[0]

    def queryset_for(self, student):
        """What CouponViewSet.get_queryset would return for this student."""
        from api.coupons.views import CouponViewSet

        view = CouponViewSet()
        request = type(
            "R", (), {"user": type("U", (), {
                "is_superuser": False,
                "is_staff": False,
                "student": student,
                "institute": self.institute,
            })()}
        )()
        view.request = request  # type: ignore[assignment]
        return view.get_queryset()

    def test_a_student_sees_only_their_own_coupons(self):
        visible = list(self.queryset_for(self.mine))
        self.assertEqual([c.pk for c in visible], [self.my_coupon.pk])

    def test_a_student_cannot_see_another_students_coupon(self):
        visible = {c.pk for c in self.queryset_for(self.mine)}
        self.assertNotIn(self.their_coupon.pk, visible)


@override_settings(FEATURES=COUPONS_ON)
class ExpireCommandTests(TestCase):
    def test_the_management_command_runs_the_sweep(self):
        from io import StringIO

        from django.core.management import call_command

        institute = make_institute()
        dead = make_batch(
            institute,
            valid_from=timezone.now() - timedelta(days=10),
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        services.issue_batch(batch=dead, quantity=2)

        out = StringIO()
        call_command("expire_coupons", stdout=out)

        self.assertIn("2 coupon(s) marked expired", out.getvalue())
        self.assertEqual(
            Coupon.objects.filter(status=Coupon.Status.EXPIRED).count(), 2
        )


@override_settings(FEATURES=COUPONS_ON)
class TermScopeTests(TestCase):
    """
    The term scope, which is optional in both halves.

    Blank means "any", and that is the default — a coupon won at an event
    should not silently stop working when the semester rolls over unless
    somebody said it should.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.category = make_category(cls.institute, name="Dues")
        cls.student = make_student(cls.program)

    def coupon_for(self, **batch_kwargs):
        batch = make_batch(self.institute, **batch_kwargs)
        return services.issue_batch(batch=batch, students=[self.student])[0]

    def test_unscoped_by_default(self):
        batch = make_batch(self.institute)
        self.assertIsNone(batch.academic_year)
        self.assertIsNone(batch.semester)
        self.assertEqual(batch.describe_term(), "Any term")

    def test_an_unscoped_coupon_works_on_any_term(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        coupon = self.coupon_for()

        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)
        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("50.00"))

    def test_a_semester_scoped_coupon_refuses_another_semester(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        other = "2nd" if fee.semester != "2nd" else "1st"
        coupon = self.coupon_for(semester=other)

        with self.assertRaises(services.CouponError) as caught:
            services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)
        self.assertEqual(caught.exception.code, "wrong_term")

    def test_a_year_scoped_coupon_refuses_another_year(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        coupon = self.coupon_for(academic_year="2019-2020")

        with self.assertRaises(services.CouponError) as caught:
            services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)
        self.assertEqual(caught.exception.code, "wrong_term")

    def test_the_two_halves_are_independent(self):
        """Scoping the semester alone leaves the year open, and vice versa."""
        fee = make_fee(self.student, self.category, amount="500.00")
        coupon = self.coupon_for(semester=fee.semester)

        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)
        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("50.00"))

    def test_the_label_words_every_combination(self):
        cases = [
            ({}, "Any term"),
            ({"academic_year": "2025-2026"}, "2025-2026"),
            ({"semester": "1st"}, "First Semester, any year"),
            (
                {"academic_year": "2025-2026", "semester": "1st"},
                "First Semester 2025-2026",
            ),
        ]
        for kwargs, expected in cases:
            with self.subTest(**kwargs):
                self.assertEqual(
                    make_batch(self.institute, **kwargs).describe_term(), expected
                )


@override_settings(FEATURES=COUPONS_ON)
class ExportEndpointTests(TestCase):
    """
    The export *endpoints*, not just the builders.

    The builders were tested and passing while the endpoints were unusable —
    the client fetched them with a plain browser navigation, which carries no
    Bearer token. These go through the API so the wiring is covered too:
    permission, content type, and the attachment header.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.category = make_category(cls.institute, name="Dues")
        cls.student = make_student(cls.program)

        # CMS_ADMIN, which is where COUPON_MANAGEMENT is granted.
        cls.staff = make_staff(cls.institute)

        cls.batch = make_batch(cls.institute, reason="Quiz bee")
        cls.batch.categories.set([cls.category])
        services.issue_batch(batch=cls.batch, students=[cls.student])

    def setUp(self):
        from django.core.cache import cache
        from rest_framework.test import APIClient

        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def test_the_spreadsheet_comes_back_as_an_attachment(self):
        response = self.client.get(
            f"/api/v1/coupon-batches/{self.batch.pk}/export/excel/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response["Content-Type"])
        self.assertIn("attachment;", response["Content-Disposition"])
        # Timestamped, so two runs do not overwrite each other.
        self.assertIn(f"coupons-{self.batch.pk}-", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"PK"))

    def test_the_printable_sheet_comes_back_as_a_pdf(self):
        response = self.client.get(
            f"/api/v1/coupon-batches/{self.batch.pk}/export/pdf/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_asking_for_the_binary_type_is_negotiable(self):
        """
        With only the project's JSON renderer configured, an `Accept:` for the
        file type is refused with a 406 before the handler ever runs.
        """
        response = self.client.get(
            f"/api/v1/coupon-batches/{self.batch.pk}/export/pdf/",
            HTTP_ACCEPT="application/pdf",
        )
        self.assertEqual(response.status_code, 200)

    def test_desk_staff_can_see_a_batch_but_not_print_it(self):
        """
        Printing a sheet of coupons is closer to minting them than to
        reading a record, so it sits with the issuing authority rather than
        with the desk. Desk staff can still look one up.
        """
        from rest_framework.test import APIClient

        from api.rbac import Roles

        desk = make_staff(self.institute, role=Roles.CMS_STAFF)
        client = APIClient()
        client.force_authenticate(desk)

        readable = client.get(f"/api/v1/coupon-batches/{self.batch.pk}/")
        self.assertEqual(readable.status_code, 200)

        refused = client.get(
            f"/api/v1/coupon-batches/{self.batch.pk}/export/excel/"
        )
        self.assertEqual(refused.status_code, 403)


@override_settings(FEATURES=COUPONS_ON)
class CapacityTests(TestCase):
    """
    A claim takes a use.

    Without that, a shared code can be promised to more people than it can
    pay: two students claim the last use, the first to apply gets it, and the
    second is left holding a coupon their account said was theirs. The
    contribution scheme reaches the same conclusion for the same reason —
    points leave the wallet when a discount is *requested*, not approved.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.category = make_category(cls.institute, name="Dues")
        cls.first = make_student(cls.program)
        cls.second = make_student(cls.program)

    def shared(self, uses=1):
        batch = make_batch(self.institute, kind=CouponBatch.Kind.QR_CAMPAIGN)
        return services.issue_batch(
            batch=batch, quantity=1, max_redemptions=uses
        )[0]

    def test_the_last_use_cannot_be_promised_twice(self):
        coupon = self.shared(uses=1)

        services.claim(raw_code=coupon.code, student=self.first)

        with self.assertRaises(services.CouponError) as caught:
            services.claim(raw_code=coupon.code, student=self.second)
        self.assertEqual(caught.exception.code, "fully_redeemed")

        coupon.refresh_from_db()
        self.assertEqual(coupon.uses_taken, 1)
        self.assertEqual(coupon.status, Coupon.Status.FULLY_REDEEMED)

    def test_a_claim_holds_the_use_until_it_is_spent(self):
        """The holder can still spend it later, which is the point of a claim."""
        fee = make_fee(self.first, self.category, amount="500.00")
        coupon = self.shared(uses=1)

        services.claim(raw_code=coupon.code, student=self.first)
        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.first)

        coupon.refresh_from_db()
        # Claim took one, apply must not take another.
        self.assertEqual(coupon.uses_taken, 1)
        fee.refresh_from_db()
        self.assertEqual(fee.discount_total, Decimal("50.00"))

    def test_capacity_is_shared_across_students(self):
        coupon = self.shared(uses=2)
        third = make_student(self.program)

        services.claim(raw_code=coupon.code, student=self.first)
        services.claim(raw_code=coupon.code, student=self.second)

        with self.assertRaises(services.CouponError) as caught:
            services.claim(raw_code=coupon.code, student=third)
        self.assertEqual(caught.exception.code, "fully_redeemed")

    def test_applying_without_claiming_still_takes_a_use(self):
        fee = make_fee(self.first, self.category, amount="500.00")
        coupon = self.shared(uses=1)

        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.first)

        coupon.refresh_from_db()
        self.assertEqual(coupon.uses_taken, 1)

        with self.assertRaises(services.CouponError) as caught:
            services.claim(raw_code=coupon.code, student=self.second)
        self.assertEqual(caught.exception.code, "fully_redeemed")

    # -- giving one back ----------------------------------------------------

    def test_releasing_a_hold_frees_it_for_somebody_else(self):
        """
        Otherwise a mis-scan burns a use for good: the student cannot spend
        it and nobody else can have it, which is worse than the double-claim
        the hold prevents.
        """
        coupon = self.shared(uses=1)
        held = services.claim(raw_code=coupon.code, student=self.first)

        services.release_claim(redemption=held)

        coupon.refresh_from_db()
        self.assertEqual(coupon.uses_taken, 0)
        self.assertEqual(coupon.status, Coupon.Status.ACTIVE)

        # And the next student really can take it.
        services.claim(raw_code=coupon.code, student=self.second)
        coupon.refresh_from_db()
        self.assertEqual(coupon.uses_taken, 1)

    def test_a_spent_coupon_cannot_be_released(self):
        fee = make_fee(self.first, self.category, amount="500.00")
        coupon = self.shared(uses=1)
        services.claim(raw_code=coupon.code, student=self.first)
        spent = services.apply(
            raw_code=coupon.code, fee_id=fee.pk, student=self.first
        )

        with self.assertRaises(services.CouponError) as caught:
            services.release_claim(redemption=spent)
        self.assertEqual(caught.exception.code, "already_used")

    def test_reversing_a_spent_coupon_frees_the_use(self):
        fee = make_fee(self.first, self.category, amount="500.00")
        coupon = self.shared(uses=1)
        redemption = services.apply(
            raw_code=coupon.code, fee_id=fee.pk, student=self.first
        )

        services.reverse_redemption(
            redemption=redemption, actor=self.staff, reason="Issued in error"
        )

        coupon.refresh_from_db()
        self.assertEqual(coupon.uses_taken, 0)
        self.assertEqual(coupon.status, Coupon.Status.ACTIVE)

    # -- recall still tells held from spent ---------------------------------

    def test_a_recall_voids_a_held_coupon_but_not_a_spent_one(self):
        fee = make_fee(self.first, self.category, amount="500.00")
        batch = make_batch(self.institute)
        spent, held, untouched = services.issue_batch(batch=batch, quantity=3)

        services.apply(raw_code=spent.code, fee_id=fee.pk, student=self.first)
        services.claim(raw_code=held.code, student=self.second)

        result = services.revoke_batch(
            batch=batch, actor=self.staff, reason="Event cancelled"
        )

        # Held and untouched are both voided; only the spent one is left.
        self.assertEqual(result["voided"], 2)
        self.assertEqual(result["already_redeemed"], 1)

        held.refresh_from_db()
        untouched.refresh_from_db()
        spent.refresh_from_db()
        self.assertEqual(held.status, Coupon.Status.REVOKED)
        self.assertEqual(untouched.status, Coupon.Status.REVOKED)
        self.assertNotEqual(spent.status, Coupon.Status.REVOKED)

    def test_a_held_coupon_can_still_be_voided(self):
        """It took a use but moved no money, so voiding is the right verb."""
        coupon = self.shared(uses=1)
        services.claim(raw_code=coupon.code, student=self.first)

        services.void_coupon(
            coupon=coupon, actor=self.staff, reason="Issued by mistake"
        )
        coupon.refresh_from_db()
        self.assertEqual(coupon.status, Coupon.Status.REVOKED)


@override_settings(FEATURES=COUPONS_ON)
class PagingTests(TestCase):
    """
    Every coupon list is paged, and the page is small.

    A batch is minted a few hundred codes at a time and a student who goes to
    every event of the term collects a drawerful. A screen that asked for all
    of them and rendered whatever came back was quietly wrong twice: it hid
    the tail, and it said nothing about hiding it.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.student = make_student(cls.program)

        cls.batch = make_batch(cls.institute)
        cls.coupons = services.issue_batch(batch=cls.batch, quantity=25)

    def setUp(self):
        from django.core.cache import cache
        from rest_framework.test import APIClient

        cache.clear()
        self.client = APIClient()

    def test_a_batchs_codes_come_back_ten_at_a_time(self):
        self.client.force_authenticate(self.staff)

        page = self.client.get(
            f"/api/v1/coupon-batches/{self.batch.pk}/coupons/"
        ).json()["data"]

        self.assertEqual(len(page["data"]), 10)
        self.assertEqual(page["total_items"], 25)
        self.assertEqual(page["total_pages"], 3)

    def test_the_last_page_of_codes_holds_the_remainder(self):
        self.client.force_authenticate(self.staff)

        page = self.client.get(
            f"/api/v1/coupon-batches/{self.batch.pk}/coupons/?current_page=3"
        ).json()["data"]

        self.assertEqual(len(page["data"]), 5)
        self.assertEqual(page["current_page"], 3)

    def test_the_pages_together_are_the_whole_batch(self):
        """No row is dropped or repeated at a boundary."""
        self.client.force_authenticate(self.staff)

        seen = []
        for number in (1, 2, 3):
            page = self.client.get(
                f"/api/v1/coupon-batches/{self.batch.pk}/coupons/"
                f"?current_page={number}"
            ).json()["data"]
            seen.extend(row["code"] for row in page["data"])

        self.assertEqual(len(seen), 25)
        self.assertEqual(set(seen), {c.code for c in self.coupons})

    def test_what_a_student_holds_is_paged_too(self):
        # A run that lets somebody hold several - the default is one each, and
        # this test is about paging, not the limit.
        self.batch.one_per_student = False
        self.batch.save(update_fields=["one_per_student"])

        for coupon in self.coupons[:12]:
            services.claim(raw_code=coupon.code, student=self.student)

        self.client.force_authenticate(self.student.user)
        page = self.client.get("/api/v1/coupon-redemptions/mine/").json()["data"]

        self.assertEqual(len(page["data"]), 10)
        self.assertEqual(page["total_items"], 12)

    def test_a_non_student_gets_the_paged_shape_not_a_bare_list(self):
        """
        The envelope has to be there either way.

        Handing back `[]` for one caller and `{data: []}` for another means
        every consumer branches on which it got, and one of them forgets.
        """
        self.client.force_authenticate(self.staff)

        page = self.client.get("/api/v1/coupon-redemptions/mine/").json()["data"]

        self.assertEqual(page["data"], [])
        self.assertEqual(page["total_items"], 0)

    def test_the_batch_list_is_paged(self):
        for index in range(12):
            make_batch(self.institute, name=f"Run {index}")

        self.client.force_authenticate(self.staff)
        page = self.client.get("/api/v1/coupon-batches/").json()["data"]

        self.assertEqual(len(page["data"]), 10)
        self.assertEqual(page["total_items"], 13)


@override_settings(FEATURES=COUPONS_ON)
class StatusWordingTests(TestCase):
    """
    A held coupon must not read as a spent one.

    Claiming takes a use, so a coupon a student is holding lands on
    FULLY_REDEEMED - true about its capacity, false about the money. Staff
    read that column to decide whether a recall is worth running, and
    "fully redeemed" says the answer is no when it is yes.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.category = make_category(cls.institute, name="Dues")
        cls.staff = make_staff(cls.institute)
        cls.student = make_student(cls.program)
        cls.batch = make_batch(cls.institute)

    def test_a_held_coupon_reads_as_claimed(self):
        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]
        services.claim(raw_code=coupon.code, student=self.student)

        coupon.refresh_from_db()
        self.assertEqual(coupon.status, Coupon.Status.FULLY_REDEEMED)
        self.assertEqual(coupon.status_label, "Claimed")
        self.assertFalse(coupon.is_spent)

    def test_a_spent_coupon_reads_as_fully_redeemed(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]
        services.apply(raw_code=coupon.code, fee_id=fee.pk, student=self.student)

        coupon.refresh_from_db()
        self.assertEqual(coupon.status_label, "Fully redeemed")
        self.assertTrue(coupon.is_spent)

    def test_an_untouched_coupon_reads_as_active(self):
        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]

        self.assertEqual(coupon.status_label, "Active")

    def test_the_api_carries_the_label_and_the_spent_flag(self):
        """
        The UI gates its Void button on `is_spent`, so the API has to say it.

        Gating on `uses_taken` instead would grey out Void for a held coupon,
        which is exactly the one a mistaken issue needs voided.
        """
        from rest_framework.test import APIClient
        from django.core.cache import cache

        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]
        services.claim(raw_code=coupon.code, student=self.student)

        cache.clear()
        client = APIClient()
        client.force_authenticate(self.staff)
        row = client.get(
            f"/api/v1/coupons/lookup/?code={coupon.code}"
        ).json()["data"]

        self.assertEqual(row["status_label"], "Claimed")
        self.assertEqual(row["uses_taken"], 1)
        self.assertFalse(row["is_spent"])


@override_settings(FEATURES=COUPONS_ON)
class CodeUniquenessTests(TestCase):
    """
    Two coupons must never share a code.

    A code is the whole coupon for a bearer batch, and the lookup resolves
    one code to one coupon; a duplicate would hand somebody else's relief to
    whoever typed it first.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.batch = make_batch(cls.institute)

    def test_a_run_has_no_repeats_within_itself(self):
        coupons = services.issue_batch(batch=self.batch, quantity=200)

        codes = [c.code for c in coupons]
        self.assertEqual(len(codes), 200)
        self.assertEqual(len(set(codes)), 200)

    def test_a_second_run_repeats_nothing_from_the_first(self):
        first = {c.code for c in services.issue_batch(batch=self.batch, quantity=50)}
        second_batch = make_batch(self.institute, name="Second run")
        second = {
            c.code for c in services.issue_batch(batch=second_batch, quantity=50)
        }

        self.assertFalse(first & second)

    def test_the_column_refuses_a_duplicate_outright(self):
        """The index is the backstop, and it has to actually be there."""
        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Coupon.objects.create(batch=self.batch, code=coupon.code)

    def test_a_prefix_is_carried_and_still_unique(self):
        coupons = services.issue_batch(
            batch=self.batch, quantity=30, code_prefix="icw"
        )

        self.assertTrue(all(c.code.startswith("ICW-") for c in coupons))
        self.assertEqual(len({c.code for c in coupons}), 30)

    def test_a_collision_with_another_run_is_retried_not_raised(self):
        """
        Two runs can check before either writes.

        The unique index is what actually catches that, and the response to
        it must be a fresh set of codes - not a 500 halfway through a print
        run somebody is standing at a printer waiting for.
        """
        from unittest.mock import patch

        clash = services._mint_codes("", 3)
        Coupon.objects.create(batch=self.batch, code=clash[1])

        # First mint hands back the codes that now collide; the retry mints
        # honestly and must succeed.
        real = services._mint_codes
        calls = {"n": 0}

        def flaky(prefix, count):
            calls["n"] += 1
            if calls["n"] == 1:
                return list(clash[:count])
            return real(prefix, count)

        with patch.object(services, "_mint_codes", side_effect=flaky):
            created = services.issue_batch(batch=self.batch, quantity=3)

        self.assertEqual(calls["n"], 2)
        self.assertEqual(len(created), 3)
        self.assertEqual(len({c.code for c in created}), 3)
        self.assertNotIn(clash[1], {c.code for c in created})

    def test_minting_a_run_does_not_cost_a_query_per_coupon(self):
        """
        Five hundred round trips to mint five hundred coupons is the
        difference between a print run and a timeout on a remote database.
        """
        with self.assertNumQueries(1):
            services._mint_codes("", 250)


@override_settings(FEATURES=COUPONS_ON)
class CouponDetailEndpointTests(TestCase):
    """
    "Who used this, and when?"

    The question somebody actually walks up to a desk with. It is not
    answerable from the coupon row - the answer lives on its redemptions -
    so a detail read has to carry them.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.category = make_category(cls.institute, name="Attendance Fine")
        cls.staff = make_staff(cls.institute)
        cls.student = make_student(cls.program)
        cls.batch = make_batch(cls.institute, reason="Quiz bee")
        cls.batch.categories.set([cls.category])

    def setUp(self):
        from django.core.cache import cache
        from rest_framework.test import APIClient

        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def test_a_spent_coupon_names_who_spent_it_and_when(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]
        services.apply(
            raw_code=coupon.code,
            fee_id=fee.pk,
            student=self.student,
            actor=self.student.user,
        )

        row = self.client.get(f"/api/v1/coupons/{coupon.pk}/").json()["data"]

        self.assertEqual(len(row["redemptions"]), 1)
        entry = row["redemptions"][0]
        self.assertEqual(entry["status"], "applied")
        self.assertEqual(entry["student_school_id"], self.student.s_studentID)
        self.assertEqual(entry["fee_category"], "Attendance Fine")
        self.assertIsNotNone(entry["applied_at"])
        # The write-down reads off the balance, not the frozen gross.
        self.assertEqual(Decimal(entry["previous_balance"]), Decimal("500.00"))
        self.assertEqual(Decimal(entry["amount"]), Decimal("50.00"))

    def test_a_held_coupon_names_who_is_holding_it(self):
        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]
        services.claim(raw_code=coupon.code, student=self.student)

        row = self.client.get(f"/api/v1/coupons/{coupon.pk}/").json()["data"]

        self.assertEqual(len(row["redemptions"]), 1)
        self.assertEqual(row["redemptions"][0]["status"], "claimed")
        self.assertIsNone(row["redemptions"][0]["applied_at"])
        # Taken, but not spent - so still voidable, and the label says so.
        self.assertEqual(row["uses_taken"], 1)
        self.assertFalse(row["is_spent"])
        self.assertEqual(row["status_label"], "Claimed")

    def test_an_untouched_coupon_has_an_empty_history(self):
        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]

        row = self.client.get(f"/api/v1/coupons/{coupon.pk}/").json()["data"]

        self.assertEqual(row["redemptions"], [])
        self.assertEqual(row["batch_reason"], "Quiz bee")

    def test_the_desk_lookup_carries_the_history_too(self):
        """Same question, and the desk is where it is usually asked."""
        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]
        services.claim(raw_code=coupon.code, student=self.student)

        row = self.client.get(
            f"/api/v1/coupons/lookup/?code={coupon.code}"
        ).json()["data"]

        self.assertEqual(len(row["redemptions"]), 1)
        self.assertEqual(row["redemptions"][0]["student_name"].strip() != "", True)

    def test_a_reversal_names_who_reversed_it(self):
        fee = make_fee(self.student, self.category, amount="500.00")
        coupon = services.issue_batch(batch=self.batch, quantity=1)[0]
        redemption = services.apply(
            raw_code=coupon.code, fee_id=fee.pk, student=self.student
        )
        services.reverse_redemption(
            redemption=redemption, actor=self.staff, reason="Wrong student"
        )

        row = self.client.get(f"/api/v1/coupons/{coupon.pk}/").json()["data"]
        entry = row["redemptions"][0]

        self.assertEqual(entry["status"], "reversed")
        self.assertEqual(entry["reversed_by_username"], self.staff.username)
        self.assertEqual(entry["reversal_reason"], "Wrong student")

    def test_the_codes_in_a_batch_can_be_searched(self):
        """A run is minted in the hundreds; usually one code is the question."""
        wanted, *_ = services.issue_batch(batch=self.batch, quantity=15)

        page = self.client.get(
            f"/api/v1/coupon-batches/{self.batch.pk}/coupons/?q={wanted.code}"
        ).json()["data"]

        self.assertEqual(page["total_items"], 1)
        self.assertEqual(page["data"][0]["code"], wanted.code)

    def test_the_code_filter_is_not_called_search(self):
        """`?search=` belongs to the batch lookup, and would 404 it.

        `get_object()` runs this viewset's own filter backends, so a `search`
        naming a coupon code is matched against the *run's* name and reason -
        nothing matches, and the batch 404s before the action runs. Naming
        the parameter separately is the fix; this pins it, because the
        collision is invisible until somebody types a code into the box.
        """
        wanted, *_ = services.issue_batch(batch=self.batch, quantity=3)

        collided = self.client.get(
            f"/api/v1/coupon-batches/{self.batch.pk}/coupons/?search={wanted.code}"
        )

        self.assertEqual(collided.status_code, 404)

    def test_the_codes_can_be_searched_by_the_student_holding_them(self):
        bound = services.issue_batch(batch=self.batch, students=[self.student])[0]
        services.issue_batch(batch=self.batch, quantity=5)

        page = self.client.get(
            f"/api/v1/coupon-batches/{self.batch.pk}/coupons/"
            f"?q={self.student.s_studentID}"
        ).json()["data"]

        self.assertEqual([row["code"] for row in page["data"]], [bound.code])


@override_settings(FEATURES=COUPONS_ON)
class HistoryPrivacyTests(TestCase):
    """
    A shared code's history is not a public list of who took it.

    `view_coupon` is a student permission - it has to be, or a student cannot
    read their own coupons - and the queryset lets a student fetch a coupon
    they hold. On a bearer code claimed by a dozen students, handing back the
    whole redemption list would tell each of them the names, school ids and
    programs of everybody else.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.first = make_student(cls.program)
        cls.second = make_student(cls.program)

        batch = make_batch(cls.institute, kind=CouponBatch.Kind.MULTI_USE)
        cls.shared = services.issue_batch(
            batch=batch, quantity=1, max_redemptions=5
        )[0]
        services.claim(raw_code=cls.shared.code, student=cls.first)
        services.claim(raw_code=cls.shared.code, student=cls.second)

    def setUp(self):
        from django.core.cache import cache
        from rest_framework.test import APIClient

        cache.clear()
        self.client = APIClient()

    def test_a_student_sees_only_their_own_claim(self):
        self.client.force_authenticate(self.first.user)

        row = self.client.get(f"/api/v1/coupons/{self.shared.pk}/").json()["data"]

        self.assertEqual(len(row["redemptions"]), 1)
        self.assertEqual(row["redemptions"][0]["student"], self.first.pk)

    def test_the_other_students_name_is_not_in_the_payload_at_all(self):
        """Not merely hidden in the UI - never sent."""
        self.client.force_authenticate(self.first.user)

        body = self.client.get(f"/api/v1/coupons/{self.shared.pk}/").content.decode()

        self.assertNotIn(self.second.s_studentID, body)
        self.assertNotIn(self.second.s_lname, body)

    def test_staff_see_every_claim(self):
        """The desk's whole question is who has this and what they did."""
        self.client.force_authenticate(self.staff)

        row = self.client.get(f"/api/v1/coupons/{self.shared.pk}/").json()["data"]

        self.assertEqual(len(row["redemptions"]), 2)
        self.assertEqual(
            {entry["student"] for entry in row["redemptions"]},
            {self.first.pk, self.second.pk},
        )

    def test_a_students_own_list_carries_what_the_card_needs(self):
        """Expiry and scope, so the row does not need a second request."""
        self.client.force_authenticate(self.first.user)

        page = self.client.get("/api/v1/coupon-redemptions/mine/").json()["data"]
        row = page["data"][0]

        self.assertEqual(row["coupon"], self.shared.pk)
        self.assertIsNotNone(row["expires_at"])
        self.assertEqual(row["applies_to"], "Any fee")
        self.assertEqual(row["term_label"], "Any term")

    def test_reading_a_coupon_does_not_cost_a_query_per_redemption(self):
        """
        The history is about a coupon we are already holding.

        Every redemption's `coupon` is the object being serialized, and its
        batch and categories came with it. Letting each row re-resolve them
        turned one desk lookup into two queries per claim.
        """
        batch = make_batch(self.institute, kind=CouponBatch.Kind.MULTI_USE)
        coupon = services.issue_batch(batch=batch, quantity=1, max_redemptions=20)[0]
        for _ in range(3):
            services.claim(raw_code=coupon.code, student=make_student(self.program))

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.force_authenticate(self.staff)
        url = f"/api/v1/coupons/{coupon.pk}/"
        # Warm the permission cache, which is per-user and not what is being
        # measured here.
        self.client.get(url)

        with CaptureQueriesContext(connection) as three:
            self.client.get(url)

        for _ in range(6):
            services.claim(raw_code=coupon.code, student=make_student(self.program))

        with CaptureQueriesContext(connection) as nine:
            self.client.get(url)

        # Three times the history for the same number of queries. The count
        # itself is not the point and will drift; that it does not grow with
        # the number of claims is.
        self.assertEqual(len(nine.captured_queries), len(three.captured_queries))


@override_settings(FEATURES=COUPONS_ON)
class MyCouponsSearchTests(TestCase):
    """Finding one coupon in a drawerful, from the header search box."""

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.student = make_student(cls.program)

        quiz = make_batch(cls.institute, name="Quiz Bee 2026", reason="2nd place")
        cls.quiz_coupon = services.issue_batch(batch=quiz, students=[cls.student])[0]

        clean = make_batch(
            cls.institute, name="Campus Clean-up", reason="Volunteered a Saturday"
        )
        cls.clean_coupon = services.issue_batch(batch=clean, students=[cls.student])[0]

        services.claim(raw_code=cls.quiz_coupon.code, student=cls.student)
        services.claim(raw_code=cls.clean_coupon.code, student=cls.student)

    def setUp(self):
        from django.core.cache import cache
        from rest_framework.test import APIClient

        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(self.student.user)

    def held(self, query=""):
        url = "/api/v1/coupon-redemptions/mine/"
        if query:
            url += f"?search={query}"
        return self.client.get(url).json()["data"]

    def test_everything_held_comes_back_without_a_search(self):
        self.assertEqual(self.held()["total_items"], 2)

    def test_searching_by_the_code_off_the_paper(self):
        page = self.held(self.quiz_coupon.code)

        self.assertEqual(page["total_items"], 1)
        self.assertEqual(page["data"][0]["code"], self.quiz_coupon.code)

    def test_searching_by_the_event_it_came_from(self):
        page = self.held("clean")

        self.assertEqual(page["total_items"], 1)
        self.assertEqual(page["data"][0]["batch_name"], "Campus Clean-up")

    def test_searching_by_why_it_was_given(self):
        page = self.held("Saturday")

        self.assertEqual(page["total_items"], 1)
        self.assertEqual(page["data"][0]["reason"], "Volunteered a Saturday")

    def test_a_search_matching_nothing_is_empty_not_everything(self):
        """A filter that silently falls back to the full list is worse than none."""
        page = self.held("nothing-like-this")

        self.assertEqual(page["total_items"], 0)
        self.assertEqual(page["data"], [])

    def test_the_search_cannot_reach_another_students_coupons(self):
        other = make_student(self.program)
        batch = make_batch(self.institute, name="Quiz Bee 2026")
        theirs = services.issue_batch(batch=batch, students=[other])[0]
        services.claim(raw_code=theirs.code, student=other)

        page = self.held("Quiz Bee")

        self.assertEqual(page["total_items"], 1)
        self.assertEqual(page["data"][0]["code"], self.quiz_coupon.code)


@override_settings(FEATURES=COUPONS_ON)
class OnePerStudentTests(TestCase):
    """
    One coupon out of a run per student, unless the run says otherwise.

    "One claim per student" was enforced per *coupon*, which is not the same
    rule: a hundred printed codes on a table could all be taken by the first
    person to reach it. No code was over-redeemed and the batch was still
    emptied by one student.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.staff = make_staff(cls.institute)
        cls.category = make_category(cls.institute, name="Dues")
        cls.student = make_student(cls.program)
        cls.other = make_student(cls.program)

    def bearer_batch(self, quantity=3, **kwargs):
        batch = make_batch(
            self.institute, kind=CouponBatch.Kind.MULTI_USE, **kwargs
        )
        return batch, services.issue_batch(batch=batch, quantity=quantity)

    def test_a_second_code_from_the_same_run_is_refused(self):
        _, coupons = self.bearer_batch()
        services.claim(raw_code=coupons[0].code, student=self.student)

        with self.assertRaises(services.CouponError) as caught:
            services.claim(raw_code=coupons[1].code, student=self.student)

        self.assertEqual(caught.exception.code, "batch_limit_reached")

    def test_the_refusal_names_the_run_not_the_code(self):
        """"Already claimed this coupon" would send them to try another."""
        _, coupons = self.bearer_batch()
        services.claim(raw_code=coupons[0].code, student=self.student)

        with self.assertRaises(services.CouponError) as caught:
            services.claim(raw_code=coupons[1].code, student=self.student)

        self.assertIn("batch", str(caught.exception).lower())

    def test_re_scanning_the_same_code_still_reads_as_already_claimed(self):
        """The narrower message is the right one when it is the same code."""
        _, coupons = self.bearer_batch()
        first = services.claim(raw_code=coupons[0].code, student=self.student)

        again = services.claim(raw_code=coupons[0].code, student=self.student)

        self.assertEqual(again.pk, first.pk)

    def test_another_student_is_unaffected(self):
        _, coupons = self.bearer_batch()
        services.claim(raw_code=coupons[0].code, student=self.student)

        held = services.claim(raw_code=coupons[1].code, student=self.other)

        self.assertEqual(held.student_id, self.other.pk)

    def test_a_different_run_is_a_different_allowance(self):
        _, first = self.bearer_batch()
        _, second = self.bearer_batch()
        services.claim(raw_code=first[0].code, student=self.student)

        held = services.claim(raw_code=second[0].code, student=self.student)

        self.assertEqual(held.coupon_id, second[0].pk)

    def test_spending_one_without_claiming_first_counts_too(self):
        """The direct-apply path has to respect the same limit."""
        fee = make_fee(self.student, self.category, amount="500.00")
        _, coupons = self.bearer_batch()
        services.apply(
            raw_code=coupons[0].code, fee_id=fee.pk, student=self.student
        )

        other_fee = make_fee(self.student, self.category, amount="500.00")
        with self.assertRaises(services.CouponError) as caught:
            services.apply(
                raw_code=coupons[1].code, fee_id=other_fee.pk, student=self.student
            )

        self.assertEqual(caught.exception.code, "batch_limit_reached")

    def test_switching_the_rule_off_allows_several(self):
        """A raffle where somebody may hold more than one ticket."""
        _, coupons = self.bearer_batch(one_per_student=False)

        services.claim(raw_code=coupons[0].code, student=self.student)
        second = services.claim(raw_code=coupons[1].code, student=self.student)

        self.assertEqual(second.coupon_id, coupons[1].pk)

    def test_releasing_a_claim_gives_the_allowance_back(self):
        _, coupons = self.bearer_batch()
        held = services.claim(raw_code=coupons[0].code, student=self.student)

        services.release_claim(redemption=held)

        swapped = services.claim(raw_code=coupons[1].code, student=self.student)
        self.assertEqual(swapped.coupon_id, coupons[1].pk)

    def test_a_reversal_gives_the_allowance_back(self):
        """The student received nothing, so they are not out of their turn."""
        fee = make_fee(self.student, self.category, amount="500.00")
        _, coupons = self.bearer_batch()
        spent = services.apply(
            raw_code=coupons[0].code, fee_id=fee.pk, student=self.student
        )

        services.reverse_redemption(
            redemption=spent, actor=self.staff, reason="Wrong student"
        )

        again = services.claim(raw_code=coupons[1].code, student=self.student)
        self.assertEqual(again.coupon_id, coupons[1].pk)

    def test_the_limit_is_a_constraint_not_a_count(self):
        """
        Two codes from one run, scanned at the same moment.

        A count would read "none held" twice and let both through; the unique
        index is what actually decides.
        """
        batch, coupons = self.bearer_batch()
        CouponClaim.objects.create(
            coupon=coupons[0], student=self.student, batch_scope=batch
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CouponClaim.objects.create(
                    coupon=coupons[1], student=self.student, batch_scope=batch
                )

    def test_an_unscoped_run_writes_no_scope_to_collide_on(self):
        batch, coupons = self.bearer_batch(one_per_student=False)
        services.claim(raw_code=coupons[0].code, student=self.student)
        services.claim(raw_code=coupons[1].code, student=self.student)

        self.assertEqual(
            CouponClaim.objects.filter(
                batch_scope=batch, student=self.student
            ).count(),
            0,
        )


@override_settings(FEATURES=COUPONS_ON)
class MineEndpointTests(TestCase):
    """
    `mine?fee=` offers only what the preview would accept.

    The Apply Coupon screen lists what the student holds so the common case is
    one tap. Listing a coupon that the very next request refuses - expired
    since it was claimed, or scoped to another category - is the failure this
    filter exists to prevent.
    """

    @classmethod
    def setUpTestData(cls):
        cls.institute = make_institute()
        cls.program = make_program(cls.institute)
        cls.fine = make_category(cls.institute, name="Attendance Fine")
        cls.dues = make_category(cls.institute, name="Dues")
        cls.student = make_student(cls.program)
        cls.fee = make_fee(cls.student, cls.fine, amount="500.00")

        def held(batch):
            coupon = services.issue_batch(batch=batch, students=[cls.student])[0]
            services.claim(raw_code=coupon.code, student=cls.student)
            return coupon

        cls.usable = held(make_batch(cls.institute, name="Any fee"))

        dues_only = make_batch(cls.institute, name="Dues only")
        dues_only.categories.set([cls.dues])
        cls.wrong_category = held(dues_only)

        # Claimed in time, expired since - the gap this filter is about.
        cls.expired = held(make_batch(cls.institute, name="Last year"))
        CouponBatch.objects.filter(pk=cls.expired.batch_id).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

    def setUp(self):
        from rest_framework.test import APIClient

        self.client = APIClient()
        self.client.force_authenticate(self.student.user)

    @staticmethod
    def codes(response):
        return {row["code"] for row in response.data["data"]}

    def test_without_a_fee_everything_held_is_listed(self):
        response = self.client.get("/api/v1/coupon-redemptions/mine/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.codes(response),
            {self.usable.code, self.wrong_category.code, self.expired.code},
        )

    def test_with_a_fee_only_what_could_be_spent_on_it_is_listed(self):
        response = self.client.get(
            f"/api/v1/coupon-redemptions/mine/?fee={self.fee.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.codes(response), {self.usable.code})

    def test_a_fee_that_already_carries_a_coupon_offers_none(self):
        services.apply(
            raw_code=self.usable.code, fee_id=self.fee.pk, student=self.student
        )
        other = make_fee(self.student, self.fine, amount="500.00")

        taken = self.client.get(f"/api/v1/coupon-redemptions/mine/?fee={self.fee.pk}")
        fresh = self.client.get(f"/api/v1/coupon-redemptions/mine/?fee={other.pk}")

        self.assertEqual(self.codes(taken), set())
        # The spent one is gone from the wallet; the rest are judged afresh.
        self.assertEqual(self.codes(fresh), set())

    def test_an_unknown_fee_is_refused_by_name(self):
        response = self.client.get("/api/v1/coupon-redemptions/mine/?fee=999999")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "fee_not_found")
