"""Flipping past-due fees to OVERDUE."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from django.db import transaction
from django.utils import timezone

from api.users.models import User

from .. import cache as fee_cache
from ..models import Fee

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverdueResult:
    updated_count: int
    fee_ids: list[int] = field(default_factory=list)


def mark_fees_as_overdue(*, actor: Optional[User] = None) -> OverdueResult:
    """
    Move PENDING fees whose due date has passed to OVERDUE.

    Scoped to the actor's institute unless they are a superuser. Without that
    scoping any staff account holding `mark_fee_overdue` would flip fees for
    every institute in the database, not just their own.

    Notifies nobody, deliberately - not the students whose fees just moved,
    and not the operator who ran it. This is the unattended path, for
    schedulers: a status change driven by a date passing is not news, and
    broadcasting it on every automatic sweep was noise nobody acted on.

    When a person asks for it, telling the students is the point - so
    `NotificationBroadcastViewSet.notify_overdue_fees` calls this and then
    broadcasts, as one action.
    """
    now = timezone.now()

    candidates = Fee.objects.filter(
        status=Fee.FeeStatus.PENDING,
        due_date__isnull=False,
        due_date__lt=now,
    )

    if actor is not None and not actor.is_superuser:
        institute_id = getattr(actor, "institute_id", None)
        if not institute_id:
            logger.warning(
                "fee.mark_overdue actor=%s has no institute; nothing to do",
                getattr(actor, "username", actor),
            )
            return OverdueResult(updated_count=0)
        candidates = candidates.filter(
            student__program__institute_id=institute_id
        )

    candidate_ids = list(candidates.values_list("id", flat=True))
    if not candidate_ids:
        return OverdueResult(updated_count=0)

    with transaction.atomic():
        # Re-select the same rows without the joins, locked, and re-check the
        # status. A payment landing between the first read and the write would
        # otherwise be clobbered straight back to OVERDUE.
        locked_ids = list(
            Fee.objects.filter(
                id__in=candidate_ids,
                status=Fee.FeeStatus.PENDING,
                due_date__lt=now,
            )
            .select_for_update()
            .values_list("id", flat=True)
        )
        if not locked_ids:
            return OverdueResult(updated_count=0)

        # .update() bypasses auto_now, so updated_at is set by hand. Both
        # Fee.Meta.ordering and FeeViewSet sort on it - leaving it stale would
        # bury freshly-overdue fees at the bottom of the list.
        Fee.objects.filter(id__in=locked_ids).update(
            status=Fee.FeeStatus.OVERDUE,
            updated_at=now,
            updated_by=actor,
        )

        # .update() builds no model instances, so no post_save fires and
        # `fees/signals.py` never sees these rows. Invalidate by hand or the
        # list keeps showing them as PENDING.
        fee_cache.invalidate_after_commit(fee_cache.LIST_NS, fee_cache.ATTENDANCE_NS)

    logger.info(
        "fee.mark_overdue actor=%s updated_count=%s",
        getattr(actor, "username", "system"),
        len(locked_ids),
    )
    return OverdueResult(updated_count=len(locked_ids), fee_ids=locked_ids)
