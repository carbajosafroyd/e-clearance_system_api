"""
Entry points for the two generation flows.

A runner owns the thread boundary: it re-fetches the ORM objects the request
thread resolved (by id, never by passing instances across threads), builds the
right strategy, drives the engine and reports the result.
"""

from __future__ import annotations

import logging
import traceback

from api.collection_categories.models import CollectionCategory
from api.institutes_attendance_events.models import InstituteAttendanceEvent
from api.users.models import User

from ...constants import ATTENDANCE_CATEGORY_NAME
from ..background import run_in_background
from . import notifications
from .attendance_fees import AttendanceFeeGeneration
from .engine import generate
from .general_fees import GeneralFeeGeneration
from .strategy import FeeGenerationStrategy
from .types import GenerationRequest

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Public API - what the viewsets call
# --------------------------------------------------------------------------


def start_general_generation(
    *,
    user_id: int,
    category_id: int,
    academic_year: str,
    semester: str,
    request: GenerationRequest,
) -> None:
    run_in_background(
        run_general_generation,
        name="fees-generate-general",
        user_id=user_id,
        category_id=category_id,
        academic_year=academic_year,
        semester=semester,
        request=request,
    )


def start_attendance_generation(
    *,
    user_id: int,
    institute_attendance_event_id: int,
    request: GenerationRequest,
) -> None:
    run_in_background(
        run_attendance_generation,
        name="fees-generate-attendance",
        user_id=user_id,
        institute_attendance_event_id=institute_attendance_event_id,
        request=request,
    )


# --------------------------------------------------------------------------
# Synchronous bodies - also the seam tests drive directly
# --------------------------------------------------------------------------


def run_general_generation(
    *,
    user_id: int,
    category_id: int,
    academic_year: str,
    semester: str,
    request: GenerationRequest,
) -> None:
    actor = _load_actor(user_id)
    if actor is None:
        return

    try:
        category = CollectionCategory.objects.get(pk=category_id)
    except CollectionCategory.DoesNotExist:
        logger.error("generate_fees: category %s no longer exists", category_id)
        notifications.notify_failed(
            actor=actor,
            label="Fee",
            error="The collection category no longer exists.",
        )
        return

    _execute(
        actor=actor,
        strategy=GeneralFeeGeneration(
            category=category,
            academic_year=academic_year,
            semester=semester,
        ),
        request=request,
    )


def run_attendance_generation(
    *,
    user_id: int,
    institute_attendance_event_id: int,
    request: GenerationRequest,
) -> None:
    actor = _load_actor(user_id)
    if actor is None:
        return

    try:
        event = InstituteAttendanceEvent.objects.prefetch_related("settings").get(
            pk=institute_attendance_event_id
        )
    except InstituteAttendanceEvent.DoesNotExist:
        logger.error(
            "generate_attendance_fees: event %s no longer exists",
            institute_attendance_event_id,
        )
        notifications.notify_failed(
            actor=actor,
            label=ATTENDANCE_CATEGORY_NAME,
            error="The attendance event no longer exists.",
        )
        return

    institute_id = getattr(actor, "institute_id", None)
    try:
        category = CollectionCategory.objects.get(
            institute_id=institute_id,
            category_name=ATTENDANCE_CATEGORY_NAME,
        )
    except CollectionCategory.DoesNotExist:
        logger.error(
            "generate_attendance_fees: institute %s has no '%s' category",
            institute_id,
            ATTENDANCE_CATEGORY_NAME,
        )
        notifications.notify_failed(
            actor=actor,
            label=ATTENDANCE_CATEGORY_NAME,
            error=(
                f"Your institute has no '{ATTENDANCE_CATEGORY_NAME}' collection "
                f"category configured."
            ),
        )
        return

    _execute(
        actor=actor,
        strategy=AttendanceFeeGeneration(category=category, event=event),
        request=request,
    )


# --------------------------------------------------------------------------


def _load_actor(user_id: int):
    try:
        return User.objects.select_related("institute").get(pk=user_id)
    except User.DoesNotExist:
        logger.error("fee generation: user %s no longer exists", user_id)
        return None


def _execute(
    *,
    actor: User,
    strategy: FeeGenerationStrategy,
    request: GenerationRequest,
) -> None:
    try:
        outcome = generate(actor=actor, strategy=strategy, request=request)
    except Exception as exc:
        logger.error(
            "fee generation failed strategy=%s\n%s",
            strategy.label,
            traceback.format_exc(),
        )
        notifications.notify_failed(
            actor=actor, label=strategy.label, error=str(exc)
        )
        return

    notifications.notify_students(
        actor=actor, strategy=strategy, request=request, outcome=outcome
    )
    notifications.notify_complete(
        actor=actor, strategy=strategy, outcome=outcome
    )

    logger.info(
        "fee generation complete strategy=%s batch=%s added=%s not_added=%s "
        "skipped=%s",
        strategy.label,
        outcome.batch_id,
        len(outcome.added),
        len(outcome.not_added),
        len(outcome.infos),
    )
