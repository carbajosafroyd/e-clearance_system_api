"""
Shared vocabulary for the fees domain.

Anything the fee rules key off - category names, notification types, batch
sizes - lives here instead of being retyped as a string literal at each call
site. Renaming a collection category should mean editing one line, not
grepping the project for a quoted word.
"""

# CollectionCategory.category_name values that have their own dedicated
# generation flow, so the generic generator must refuse them.
ATTENDANCE_CATEGORY_NAME = "Attendance"
LOCKER_CATEGORY_NAME = "Locker"

SPECIAL_CATEGORY_NAMES = (ATTENDANCE_CATEGORY_NAME, LOCKER_CATEGORY_NAME)

# Every notification raised by this app is tagged with this source.
NOTIFICATION_SOURCE = "collection"


class FeeNotificationType:
    """`Notification.type` values raised by the fees app."""

    GENERATION_STARTED = "fee_generation_started"
    GENERATION_COMPLETE = "fee_generation_complete"
    GENERATION_FAILED = "fee_generation_failed"
    FEE_ISSUED = "fee_issued"


# Rows per INSERT when bulk-creating fees.
BULK_CREATE_BATCH_SIZE = 1000

# Year levels a generation run can be narrowed to.
MIN_YEAR_LEVEL = 1
MAX_YEAR_LEVEL = 4
