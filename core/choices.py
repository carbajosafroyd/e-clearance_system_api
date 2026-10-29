import datetime
from django.db.models import TextChoices


class Semester(TextChoices):
    FIRST = "1st", "First Semester"
    SECOND = "2nd", "Second Semester"


class AcademicYear:
    START_YEAR = 2022  # 1976

    @classmethod
    def get_current_year(cls):
        return datetime.datetime.now().year

    @classmethod
    def choices(cls):
        current_year = cls.get_current_year()
        return [
            (f"{year}-{year + 1}", f"{year}-{year + 1}")
            for year in range(cls.START_YEAR, current_year + 1)
        ]

    @classmethod
    def get_default(cls):
        curr = cls.get_current_year()
        return f"{curr}-{curr + 1}"


def get_default_academic_year():
    current_year = datetime.datetime.now().year
    return f"{current_year}-{current_year + 1}"


class SystemScope(TextChoices):
    """Which system a cross-cutting record belongs to.

    Student contributions are scored per system - a bug report against the
    locker system earns locker points, and those points are spent on locker
    fees - so anything that carries points carries one of these.

    The values mirror ``AuditLog.System`` deliberately: the same word means
    the same system in the audit trail and in the points ledger. Adding a
    system means adding it in both places.
    """

    COLLECTION = "collection", "Collection Management System"
    LOCKER = "locker", "Locker Management System"
    ATTENDANCE = "attendance", "Attendance Management System"
    CLEARANCE = "clearance", "Clearance Management System"
    VOTING = "voting", "Voting System"
    PLATFORM = "platform", "Platform"
