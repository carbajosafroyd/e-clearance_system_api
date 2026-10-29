"""Value objects passed around the generation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class StudentRow:
    """The only two student columns generation actually needs."""

    id: int
    school_id: str


@dataclass(frozen=True)
class GenerationRequest:
    """
    What the caller chose, flattened to plain values.

    Deliberately free of ORM instances: this crosses a thread boundary, and a
    model instance carried over would drag the request thread's database
    connection along with it.
    """

    due_date: datetime
    all_students: bool = True
    year_levels: Sequence[int] = ()
    #: Honour FeeExemptedStudent rows for this category (waive instead of bill).
    #: Exposed to the API as `exempt_students`.
    apply_exemptions: bool = False


@dataclass(frozen=True)
class FeeDraft:
    """A fee we intend to create, before anything touches the database."""

    student_id: int
    student_school_id: str
    amount: Decimal
    waived: bool = False
    reason: Optional[str] = None


@dataclass
class GenerationOutcome:
    """What a run actually did - the payload behind the summary notification."""

    batch_id: Optional[int] = None
    infos: list[str] = field(default_factory=list)
    added: list[dict[str, Any]] = field(default_factory=list)
    not_added: list[dict[str, Any]] = field(default_factory=list)

    @property
    def waived_count(self) -> int:
        return sum(1 for row in self.added if row["waived"])

    def as_notification_payload(self) -> dict[str, Any]:
        """JSON-safe view for Notification.data (Decimals are not JSON)."""
        return {
            "batch_id": self.batch_id,
            "infos": self.infos,
            "added": [
                {**row, "amount": str(row["amount"])} for row in self.added
            ],
            "not_added": self.not_added,
        }
