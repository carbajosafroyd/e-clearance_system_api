"""
Seed over-the-counter payments against the fees `seed_fees` created.

Goes through `payments.services.record_payment` rather than writing Payment
rows directly, so the fee balances and statuses end up consistent with the
ledger - which is exactly the invariant the reports lean on.

A slice of the payments is then cancelled through `cancel_payment`, so the
demo data contains the reversal case too (cancelled rows, restored balances)
instead of only the happy path.
"""

from __future__ import annotations

from decimal import Decimal

from api.fees.models import Fee
from api.payments import services as payment_services
from api.payments.models import Payment
from api.rbac.roles import Roles
from api.seeding.support import SeedCommand
from api.users.models import User

#: Share of pending fees that get some money against them.
PAID_SHARE = 0.45

#: Of those, the share that are only part-paid.
PARTIAL_SHARE = 0.35

#: Of the completed payments, the share later cancelled.
CANCELLED_SHARE = 0.06


class Command(SeedCommand):
    seed_name = "payments"
    help = "Record demo over-the-counter payments against seeded fees."
    depends_on = ("seed_fees", "seed_users")

    def reset(self, **options):
        deleted, _ = Payment.objects.all().delete()
        self.warn(
            f"deleted {deleted} payment row(s) - fee balances are left as they "
            "are; re-run seed_fees --reset for a clean slate"
        )

    def _cashier_for(self, institute_id: int) -> User | None:
        return (
            User.objects.filter(
                institute_id=institute_id,
                groups__name__in=[Roles.CMS_STAFF, Roles.CMS_ADMIN],
            )
            .order_by("id")
            .first()
        )

    def seed(self, **options):
        # Payments are activity, not reference data: there is no natural key
        # to get_or_create on, so a second run would simply pay more of the
        # remaining balances. Re-run with --reset to redo them.
        if Payment.objects.exists():
            self.info("payments already recorded; nothing to do (use --reset)")
            return

        payable = list(
            Fee.objects.filter(
                status__in=[Fee.FeeStatus.PENDING, Fee.FeeStatus.OVERDUE],
                balance__gt=0,
            )
            .select_related("student__program")
            .order_by("id")
        )
        if not payable:
            self.warn("no payable fees found; run `python manage.py seed_fees` first")
            return

        cashiers: dict[int, User | None] = {}
        chosen = self.sample(payable, int(len(payable) * PAID_SHARE) or 1)

        recorded: list[Payment] = []
        skipped = 0

        for fee in chosen:
            institute_id = fee.student.program.institute_id
            if institute_id not in cashiers:
                cashiers[institute_id] = self._cashier_for(institute_id)

            cashier = cashiers[institute_id]
            if cashier is None:
                skipped += 1
                continue

            amount = fee.balance
            if self.chance(PARTIAL_SHARE):
                # Round to whole pesos so the demo figures stay readable.
                amount = (fee.balance / Decimal("2")).quantize(Decimal("1"))
                if amount <= 0:
                    amount = fee.balance

            recorded.append(
                payment_services.record_payment(
                    fee=fee,
                    amount=amount,
                    method=self.pick(
                        [Payment.PaymentMethod.CASH, Payment.PaymentMethod.GCASH]
                    ),
                    actor=cashier,
                )
            )

        self.created("payment", len(recorded))
        if skipped:
            self.warn(
                f"{skipped} fee(s) skipped: no collection staff in their institute"
            )

        self._cancel_some(recorded)

    def _cancel_some(self, payments: list[Payment]) -> None:
        if not payments:
            return

        count = int(len(payments) * CANCELLED_SHARE)
        if count < 1:
            return

        cancelled = 0
        for payment in self.sample(payments, count):
            payment_services.cancel_payment(
                payment=payment,
                actor=payment.received_by,
                reason="Seeded example: wrong fee selected at the counter.",
            )
            cancelled += 1

        self.created("cancelled payment", cancelled)
