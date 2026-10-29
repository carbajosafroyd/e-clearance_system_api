"""
Fee generation.

    runner.py          thread boundary + flow entry points
    engine.py          the pipeline every flow shares
    strategy.py        the contract a flow implements
    general_fees.py    flat per-category fees
    attendance_fees.py per-missed-period attendance fines
    notifications.py   operator + student fan-out
    types.py           value objects crossing the thread boundary
"""

from .attendance_fees import AttendanceFeeGeneration
from .engine import GenerationError, generate
from .general_fees import GeneralFeeGeneration
from .runner import (
    run_attendance_generation,
    run_general_generation,
    start_attendance_generation,
    start_general_generation,
)
from .strategy import FeeGenerationStrategy
from .types import FeeDraft, GenerationOutcome, GenerationRequest, StudentRow

__all__ = [
    "AttendanceFeeGeneration",
    "FeeDraft",
    "FeeGenerationStrategy",
    "GeneralFeeGeneration",
    "GenerationError",
    "GenerationOutcome",
    "GenerationRequest",
    "StudentRow",
    "generate",
    "run_attendance_generation",
    "run_general_generation",
    "start_attendance_generation",
    "start_general_generation",
]
