"""
The shared fee-generation pipeline.

Every flow runs the same seven steps; only the pieces behind
`FeeGenerationStrategy` differ:

    select students -> load exemptions -> draft fees -> lock -> re-check
    -> insert -> reconcile

Reconciliation is the part worth knowing about. MySQL's bulk insert cannot
report which individual rows failed, so instead of trusting the return value
of `bulk_create` we re-query what actually landed under the batch and diff it
against what we intended. Everything intended-but-missing is reported back to
the operator rather than silently dropped.
"""

from __future__ import annotations

import logging
import traceback
from typing import Optional, Sequence

from django.db import transaction

from api.collection_categories.models import FeeExemptedStudent
from api.students.models import Student
from api.users.models import User

from ... import cache as fee_cache
from ...constants import BULK_CREATE_BATCH_SIZE
from ...models import Fee, GeneratedFeeBatch
from .strategy import FeeGenerationStrategy
from .types import FeeDraft, GenerationOutcome, GenerationRequest, StudentRow

logger = logging.getLogger(__name__)


class GenerationError(RuntimeError):
    """Raised when a run cannot start at all (bad actor, missing institute)."""


def generate(
    *,
    actor: User,
    strategy: FeeGenerationStrategy,
    request: GenerationRequest,
) -> GenerationOutcome:
    institute_id = getattr(actor, "institute_id", None)
    if not institute_id:
        raise GenerationError(
            "Cannot generate fees: your account is not attached to an institute."
        )

    outcome = GenerationOutcome()

    students = _select_students(institute_id=institute_id, request=request)
    if not students:
        logger.info("generation.no_students strategy=%s", strategy.label)
        return outcome

    student_ids = [student.id for student in students]
    strategy.prefetch(student_ids)

    exemptions = (
        _load_exemptions(strategy=strategy, student_ids=student_ids)
        if request.apply_exemptions
        else {}
    )
    already_billed = strategy.existing_fee_student_ids(student_ids)

    drafts = _draft_fees(
        students=students,
        strategy=strategy,
        exemptions=exemptions,
        already_billed=already_billed,
        outcome=outcome,
    )
    if not drafts:
        logger.info("generation.nothing_to_bill strategy=%s", strategy.label)
        return outcome

    _persist(
        actor=actor,
        institute_id=institute_id,
        strategy=strategy,
        request=request,
        drafts=drafts,
        outcome=outcome,
    )
    return outcome


def _select_students(
    *, institute_id: int, request: GenerationRequest
) -> list[StudentRow]:
    queryset = Student.objects.filter(
        program__institute_id=institute_id,
        s_status=Student.StudentStatus.ENROLLED,
    )
    if not request.all_students:
        queryset = queryset.filter(s_lvl__in=list(request.year_levels))

    return [
        StudentRow(id=row["id"], school_id=row["s_studentID"])
        for row in queryset.values("id", "s_studentID").distinct()
    ]


def _load_exemptions(
    *, strategy: FeeGenerationStrategy, student_ids: Sequence[int]
) -> dict[int, str]:
    rows = FeeExemptedStudent.objects.filter(
        student_id__in=student_ids,
        category=strategy.category,
        academic_year=strategy.academic_year,
        semester=strategy.semester,
        status=True,
    ).values("student_id", "reason")
    return {row["student_id"]: row["reason"] for row in rows}


def _draft_fees(
    *,
    students: Sequence[StudentRow],
    strategy: FeeGenerationStrategy,
    exemptions: dict[int, str],
    already_billed: set[int],
    outcome: GenerationOutcome,
) -> list[FeeDraft]:
    drafts: list[FeeDraft] = []
    scope = f"{strategy.academic_year} - {strategy.semester}"

    for student in students:
        if student.id in already_billed:
            outcome.infos.append(
                f"SKIPPED. student_school_id:{student.school_id} already has a "
                f"{strategy.label} fee for {scope}"
            )
            continue

        amount = strategy.amount_for(student.id)

        if strategy.skip_zero_amount and amount <= 0:
            outcome.infos.append(
                f"SKIPPED. student_school_id:{student.school_id} owes nothing "
                f"for {scope}; no fee raised"
            )
            continue

        # `exemptions` is empty unless the caller asked for them to be applied,
        # so presence here is the whole test.
        reason = exemptions.get(student.id)
        if reason is not None:
            outcome.infos.append(
                f"WAIVED. student_school_id:{student.school_id} is exempted for "
                f"{scope}. reason: {reason}"
            )

        drafts.append(
            FeeDraft(
                student_id=student.id,
                student_school_id=student.school_id,
                amount=amount,
                waived=reason is not None,
                reason=reason,
            )
        )

    return drafts


def _persist(
    *,
    actor: User,
    institute_id: int,
    strategy: FeeGenerationStrategy,
    request: GenerationRequest,
    drafts: list[FeeDraft],
    outcome: GenerationOutcome,
) -> None:
    with transaction.atomic():
        strategy.lock_scope()

        # Now that the scope is locked, re-check: a run that started while we
        # were computing may already have billed some of these students.
        billed_since = strategy.existing_fee_student_ids(
            [draft.student_id for draft in drafts]
        )
        if billed_since:
            for draft in drafts:
                if draft.student_id in billed_since:
                    outcome.infos.append(
                        f"SKIPPED. student_school_id:{draft.student_school_id} "
                        f"was billed by a concurrent generation run"
                    )
            drafts = [d for d in drafts if d.student_id not in billed_since]
        if not drafts:
            return

        batch = GeneratedFeeBatch.objects.create(
            category=strategy.category,
            institute_id=institute_id,
            academic_year=strategy.academic_year,
            semester=strategy.semester,
            generated_by=actor,
            updated_by=actor,
        )
        outcome.batch_id = batch.pk

        extra_columns = strategy.fee_kwargs()
        fees = [
            Fee(
                student_id=draft.student_id,
                category=strategy.category,
                academic_year=strategy.academic_year,
                semester=strategy.semester,
                total_amount=draft.amount,
                balance=draft.amount,
                issued_by=actor,
                updated_by=actor,
                due_date=request.due_date,
                gen_fee_batch=batch,
                status=(
                    Fee.FeeStatus.WAIVED if draft.waived else Fee.FeeStatus.PENDING
                ),
                remarks=draft.reason,
                **extra_columns,
            )
            for draft in drafts
        ]

        bulk_error: Optional[str] = None
        try:
            # Nested atomic = its own savepoint. If the insert blows up partway
            # through a batch_size split, only this savepoint rolls back - the
            # batch row above survives so we can still re-query and report.
            with transaction.atomic():
                Fee.objects.bulk_create(fees, batch_size=BULK_CREATE_BATCH_SIZE)
        except Exception as exc:
            bulk_error = str(exc)
            logger.error(
                "bulk_create failed for batch %s\n%s",
                batch.pk,
                traceback.format_exc(),
            )

        persisted = list(Fee.objects.filter(gen_fee_batch=batch))
        _reconcile(
            drafts=drafts,
            persisted=persisted,
            bulk_error=bulk_error,
            outcome=outcome,
        )

        if not persisted:
            batch.delete()
            outcome.batch_id = None
            return

        batch.total_created_fees = len(persisted)
        batch.save(update_fields=["total_created_fees"])
        strategy.on_persisted(persisted)

        # `bulk_create` above and the `bulk_update` inside `on_persisted` write
        # straight to SQL, so no post_save fires and `fees/signals.py` never
        # runs for the rows we just inserted. Without this the fee list would
        # keep serving the pre-generation page until its TTL ran out.
        fee_cache.invalidate_after_commit(
            fee_cache.LIST_NS, fee_cache.BATCH_NS, fee_cache.ATTENDANCE_NS
        )


def _reconcile(
    *,
    drafts: Sequence[FeeDraft],
    persisted: Sequence[Fee],
    bulk_error: Optional[str],
    outcome: GenerationOutcome,
) -> None:
    """Diff what we intended against what the database actually holds."""
    drafts_by_student = {draft.student_id: draft for draft in drafts}
    persisted_student_ids = {fee.student_id for fee in persisted}

    outcome.added = [
        {
            "student_id": fee.student_id,
            "student_school_id": getattr(
                drafts_by_student.get(fee.student_id), "student_school_id", None
            ),
            "fee_id": fee.pk,
            "amount": fee.total_amount,
            "waived": fee.status == Fee.FeeStatus.WAIVED,
        }
        for fee in persisted
    ]

    outcome.not_added = [
        {
            "student_id": draft.student_id,
            "student_school_id": draft.student_school_id,
            "waived": draft.waived,
            # A MySQL bulk insert cannot tell us which row failed - only
            # whether the whole statement did.
            "reason": bulk_error or "Fee was not persisted for an unknown reason",
        }
        for draft in drafts
        if draft.student_id not in persisted_student_ids
    ]
