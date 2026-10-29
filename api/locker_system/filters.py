from django.db.models import QuerySet

from api.utils.helpers.app_settings import get_setting
from .models import Locker, LockerSubscription
from typing import TypedDict


class AcademicPeriod(TypedDict):
    academic_year: str
    semester: str


def get_current_period(self) -> AcademicPeriod:
    """
    Returns AcademicPeriod from query params or fallback to app setting.
    """
    year = self.request.GET.get("year")
    semester = self.request.GET.get("semester")

    if year and semester:
        return {"academic_year": year, "semester": semester}

    return get_setting("current_academic_period")


def filter_available_lockers_this_year_sem(
    # *,
    # queryset: QuerySet[Locker],
    self,
    institute,
    period: AcademicPeriod,
) -> QuerySet[Locker]:

    lockers_rented_ids = LockerSubscription.objects.filter(
        locker__institute=institute,
        school_year=period["academic_year"],
        semester=period["semester"],
    ).values_list("locker_id", flat=True)

    return self.queryset.filter(institute=institute).exclude(id__in=lockers_rented_ids)
