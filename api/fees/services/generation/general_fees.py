"""Flat per-category fee generation (uniform, ID, organisation dues, ...)."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from api.collection_categories.models import CollectionCategory

from ...models import Fee
from .strategy import FeeGenerationStrategy


class GeneralFeeGeneration(FeeGenerationStrategy):
    """
    Everyone in scope owes the category's flat `collection_fee`.

    Uniqueness is per (student, category, academic year, semester).
    """

    def __init__(
        self,
        *,
        category: CollectionCategory,
        academic_year: str,
        semester: str,
    ) -> None:
        self.category = category
        self.academic_year = academic_year
        self.semester = semester

    def existing_fee_student_ids(self, student_ids: Sequence[int]) -> set[int]:
        return set(
            Fee.objects.filter(
                student_id__in=student_ids,
                category=self.category,
                academic_year=self.academic_year,
                semester=self.semester,
            ).values_list("student_id", flat=True)
        )

    def amount_for(self, student_id: int) -> Decimal:
        return self.category.collection_fee

    def lock_scope(self) -> None:
        list(
            CollectionCategory.objects.select_for_update().filter(
                pk=self.category.pk
            )
        )
