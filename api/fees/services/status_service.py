"""
The fee status state machine: waive and unwaive.

Waiving forgives the *remaining* balance; it never undoes money that
actually changed hands - that is a payment/submission cancellation, a
separate action. So a waive is a status change plus required remarks and
nothing else: the balance stays where it is, and the report layer's
`billable()` exclusion keeps the forgiven amount out of the money math.

Everything else about a fee's status is automatic and stays that way:
PAID is set by the payment service when the balance reaches zero, PENDING
is restored by payment cancellation, OVERDUE comes only from
`mark_fees_as_overdue`. The update serializer exposes none of it.
"""

from __future__ import annotations

from django.db import transaction
from rest_framework import serializers

from ..models import Fee


def waive_fee(*, fee, actor, remarks) -> Fee:
    """PENDING/OVERDUE -> WAIVED. Remarks required; nothing to forgive is an error."""
    if not (remarks or "").strip():
        raise serializers.ValidationError(
            {"remarks": "Remarks are required to waive a fee."}
        )

    with transaction.atomic():
        locked = Fee.objects.select_for_update().get(pk=fee.pk)

        if locked.status not in (Fee.FeeStatus.PENDING, Fee.FeeStatus.OVERDUE):
            raise serializers.ValidationError(
                {
                    "detail": (
                        f"Only pending or overdue fees can be waived "
                        f"(this one is {locked.status})."
                    )
                }
            )
        if locked.balance <= 0:
            raise serializers.ValidationError(
                {"detail": "This fee has no balance left to forgive."}
            )

        locked.status = Fee.FeeStatus.WAIVED
        locked.remarks = remarks
        locked.updated_by = actor
        locked.save(update_fields=["status", "remarks", "updated_by", "updated_at"])

    return locked


def unwaive_fee(*, fee, actor) -> Fee:
    """WAIVED -> PENDING. The overdue automation re-flags past-due fees itself."""
    with transaction.atomic():
        locked = Fee.objects.select_for_update().get(pk=fee.pk)

        if locked.status != Fee.FeeStatus.WAIVED:
            raise serializers.ValidationError(
                {"detail": "Only waived fees can be unwaived."}
            )
        if locked.balance <= 0:
            raise serializers.ValidationError(
                {"detail": "This fee has no balance owing; nothing to restore."}
            )

        locked.status = Fee.FeeStatus.PENDING
        locked.updated_by = actor
        locked.save(update_fields=["status", "updated_by", "updated_at"])

    return locked
