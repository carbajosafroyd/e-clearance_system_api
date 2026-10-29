"""
Query-param parsing for the audit-log list and report endpoints.

Both endpoints accept the same filters, so the rules live here once:

* Choice params (`system`, `action`, `actor_type`) accept MULTIPLE values -
  either repeated (`?action=create&action=update`) or comma-separated
  (`?action=create,update`), and the two styles can be mixed. Unknown values
  are rejected with a 400 rather than silently matching nothing, so a typo
  in a dashboard filter is visible instead of reading as "no activity".
* `entity_type` also accepts multiple values but is free-form (it holds
  model class names), so it is not validated against a fixed set.
* `actor` searches the users table by username, first name and last name -
  the audit row itself only stores a numeric actor_id, which nobody wants
  to type into a search box. Multiple words must all match, each against
  any of the three fields, so "juan cruz" finds Juan Cruz.
* Dates are validated as real ISO dates and as a coherent range.
"""

from datetime import date
from typing import Optional

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from .models import AuditLog


def parse_multi(query_params, name: str) -> list[str]:
    """Every value of `name`, repeated params and comma-separated alike."""
    values: list[str] = []
    for raw in query_params.getlist(name):
        values.extend(part.strip() for part in raw.split(","))
    return [value for value in values if value]


def _validate_choices(values: list[str], choices, param: str) -> list[str]:
    allowed = {choice.value for choice in choices}
    unknown = [value for value in values if value not in allowed]
    if unknown:
        raise ValidationError(
            {
                param: (
                    f"Unsupported value(s): {', '.join(unknown)}. "
                    f"Allowed: {', '.join(sorted(allowed))}."
                )
            }
        )
    return values


def _parse_date(query_params, name: str) -> Optional[date]:
    raw = query_params.get(name)
    if not raw:
        return None
    parsed = parse_date(raw)
    if parsed is None:
        raise ValidationError({name: "Enter a valid date in YYYY-MM-DD format."})
    return parsed


def _parse_actor_id(query_params) -> Optional[int]:
    raw = query_params.get("actor_id")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError({"actor_id": "Enter a whole number."})


def parse_audit_filters(query_params) -> dict:
    """
    The validated, normalised filter set both the list endpoint and the
    report endpoints run on. Raises a DRF ValidationError (-> 400) on any
    value that could never match.
    """
    date_from = _parse_date(query_params, "date_from")
    date_to = _parse_date(query_params, "date_to")
    if date_from and date_to and date_from > date_to:
        raise ValidationError({"date_from": "date_from cannot be later than date_to."})

    return {
        "systems": _validate_choices(
            parse_multi(query_params, "system"), AuditLog.System, "system"
        ),
        "entity_types": parse_multi(query_params, "entity_type"),
        "entity_id": query_params.get("entity_id") or None,
        "actions": _validate_choices(
            parse_multi(query_params, "action"), AuditLog.Action, "action"
        ),
        "actor_types": _validate_choices(
            parse_multi(query_params, "actor_type"),
            AuditLog.ActorTypeChoices,
            "actor_type",
        ),
        "actor_id": _parse_actor_id(query_params),
        "actor": (query_params.get("actor") or "").strip() or None,
        "date_from": date_from,
        "date_to": date_to,
    }


def actor_ids_matching(search: str) -> list[int]:
    """
    User ids whose username / first name / last name match every word of
    `search`. An empty result is meaningful: the caller filters to nothing.
    """
    users = get_user_model().objects.all()
    for term in search.split():
        users = users.filter(
            Q(username__icontains=term)
            | Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
        )
    return list(users.values_list("id", flat=True))
