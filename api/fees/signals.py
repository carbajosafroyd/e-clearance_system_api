"""
Cache invalidation for the fees app.

Every receiver here does the same small thing: a row changed, so bump the
version of each namespace that could now be handing out stale data. How the
version bump drops a whole family of cache entries at once is explained in
`fees/cache.py`; this module is only the wiring.

Registered from `FeesConfig.ready()` in `fees/apps.py`.

Two things to know before adding to this file
---------------------------------------------
1. **Signals do not fire for bulk writes.** `bulk_create()`, `bulk_update()`
   and `queryset.update()` all go straight to SQL without building model
   instances, so nothing below runs. The two places in this app that write
   that way invalidate by hand instead:

       services/generation/engine.py   (bulk_create of the generated fees)
       services/overdue_service.py     (.update() to flip fees to OVERDUE)

   If you add a third, invalidate there too or the lists will go stale.

2. **A cascade counts as a delete.** Deleting a GeneratedFeeBatch takes its
   fees with it, and Django sends `post_delete` for each collected Fee, so
   `_fee_changed` still fires for every one of them.
"""

from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from api.attendance_records.models import AttendanceRecord
from api.institutes_attendance_event_settings.models import (
    InstituteAttendanceEventSetting,
)

from . import cache as fee_cache
from .models import Fee, GeneratedFeeBatch


@receiver(post_save, sender=Fee, dispatch_uid="fees.cache.fee_saved")
@receiver(post_delete, sender=Fee, dispatch_uid="fees.cache.fee_deleted")
def _fee_changed(sender, instance, **kwargs):
    """
    Any fee written by anyone - the admin, a payment drawing down the balance,
    a staff edit, a cascade from a deleted batch.

    Attendance detail carries the fee's own total, so that goes too.
    """
    fee_cache.invalidate_after_commit(fee_cache.LIST_NS, fee_cache.ATTENDANCE_NS)


@receiver(post_save, sender=GeneratedFeeBatch, dispatch_uid="fees.cache.batch_saved")
@receiver(
    post_delete, sender=GeneratedFeeBatch, dispatch_uid="fees.cache.batch_deleted"
)
def _batch_changed(sender, instance, **kwargs):
    """
    A batch write moves the batch list, and the fee list shows the batch a fee
    belongs to - so both go.
    """
    fee_cache.invalidate_after_commit(fee_cache.BATCH_NS, fee_cache.LIST_NS)


@receiver(post_save, sender=AttendanceRecord, dispatch_uid="fees.cache.record_saved")
@receiver(
    post_delete, sender=AttendanceRecord, dispatch_uid="fees.cache.record_deleted"
)
def _attendance_record_changed(sender, instance, **kwargs):
    """
    /fees/{id}/attendance-details/ is assembled from these rows, so an edited
    record changes the answer even though no fee was touched.

    Only the attendance namespace is bumped. Records are written constantly
    during an event, and dragging the fee list down with them would leave that
    cache empty all day for no benefit.
    """
    fee_cache.invalidate_after_commit(fee_cache.ATTENDANCE_NS)


@receiver(
    post_save,
    sender=InstituteAttendanceEventSetting,
    dispatch_uid="fees.cache.event_setting_saved",
)
@receiver(
    post_delete,
    sender=InstituteAttendanceEventSetting,
    dispatch_uid="fees.cache.event_setting_deleted",
)
def _event_setting_changed(sender, instance, **kwargs):
    """/fees/{id}/attendance-settings/ is a straight read of these rows."""
    fee_cache.invalidate_after_commit(fee_cache.ATTENDANCE_NS)
