"""
Helpers for building detailed audit payloads from model instances.

Deliberately imports nothing from other apps so any module (fees, payments,
payment_submissions, ...) can pull these in without circular-import risk.
"""
import datetime
import decimal

from .models import AuditLog


def resolve_actor(user):
    """Map a request user to the (actor_type, actor_id) pair AuditLog stores."""
    if user is None or not getattr(user, "is_authenticated", False):
        return AuditLog.ActorTypeChoices.SYSTEM, None
    if user.is_superuser:
        return AuditLog.ActorTypeChoices.ADMIN, user.id
    if user.is_staff:
        return AuditLog.ActorTypeChoices.STAFF, user.id
    if hasattr(user, "student"):
        return AuditLog.ActorTypeChoices.STUDENT, user.id
    return AuditLog.ActorTypeChoices.SYSTEM, user.id


def _json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return value
    return str(value)


def snapshot_instance(instance, exclude=("updated_at",)) -> dict:
    """
    Full JSON-safe dump of a model instance's concrete fields.

    Foreign keys are captured as their raw ids (no extra queries).
    `updated_at` is excluded by default because it changes on every save and
    would show up in every update diff without saying anything.
    """
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in exclude:
            continue
        data[field.name] = _json_safe(field.value_from_object(instance))
    return data


def diff_snapshots(old: dict, new: dict) -> tuple[dict, dict]:
    """Reduce two snapshots to only the fields whose value actually changed."""
    changed_old, changed_new = {}, {}
    for key, new_value in new.items():
        old_value = old.get(key)
        if old_value != new_value:
            changed_old[key] = old_value
            changed_new[key] = new_value
    return changed_old, changed_new
