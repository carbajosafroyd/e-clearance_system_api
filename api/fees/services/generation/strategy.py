"""The contract every fee-generation flow implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Sequence

from api.collection_categories.models import CollectionCategory

from ...models import Fee


class FeeGenerationStrategy(ABC):
    """
    The parts of fee generation that differ between fee types.

    `engine.generate()` owns everything the flows share: student selection,
    exemptions, the batch + savepoint dance, reconciliation, reporting. A
    strategy only answers the questions that are genuinely type-specific:

      * which students already have this fee, so nobody is billed twice;
      * what does a given student owe;
      * what extra columns belong on the Fee row;
      * what else must be written in the same transaction.
    """

    #: The category every fee in this run is filed under.
    category: CollectionCategory
    academic_year: str
    semester: str

    #: When True, a student whose computed amount is zero gets no fee row.
    #: Attendance sets this: a student with perfect attendance owes nothing,
    #: and a 0.00 PENDING fee would otherwise be created, notified about, and
    #: eventually marked OVERDUE for money they never owed.
    skip_zero_amount: bool = False

    @property
    def label(self) -> str:
        """Human noun used in notification copy, e.g. "Attendance"."""
        return self.category.category_name

    def prefetch(self, student_ids: Sequence[int]) -> None:
        """Bulk-load whatever `amount_for` needs. Called once, before the loop."""

    @abstractmethod
    def existing_fee_student_ids(self, student_ids: Sequence[int]) -> set[int]:
        """Students in `student_ids` that already hold a fee for this scope."""

    @abstractmethod
    def amount_for(self, student_id: int) -> Decimal:
        """What this student owes. Zero is a valid answer."""

    def fee_kwargs(self) -> dict[str, Any]:
        """Extra Fee columns specific to this flow."""
        return {}

    def lock_scope(self) -> None:
        """
        Take a row lock that serialises concurrent runs over the same scope.

        Called inside the write transaction. Two operators pressing Generate at
        the same moment would otherwise both read "nobody has this fee yet" and
        both insert - and there is no unique constraint to catch it (see the
        note on Fee.Meta in fees/models.py). The second run blocks here, then
        finds the first run's fees in the re-check and drops them from its own
        batch.
        """

    def on_persisted(self, fees: Sequence[Fee]) -> None:
        """Side effects that must land in the same transaction as the fees."""
