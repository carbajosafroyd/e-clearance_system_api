"""
The audit-log report: the same filtered trail the list endpoint serves,
summarised for reading rather than paging.

One builder feeds all three formats (JSON / PDF / Excel), so no two of them
can disagree: counts by action, system and actor type, activity per day, the
most active users, and the entries themselves with the actor resolved to a
username and full name.

The entry list is capped at AUDIT_REPORT_MAX_RECORDS - a report over years
of logs should not time out building a hundred-thousand-row PDF. The cap is
never silent: `summary.records_truncated` says it happened and
`summary.total_entries` still counts everything, so "showing 5,000 of
83,412" can be printed instead of a lie.
"""

from django.db.models import Count, Max, Min
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import AuditLog
from .services import AuditLogService

# The most entries a single report will carry. Everything above this stays
# countable (the aggregates cover the full filtered set) but is not listed
# row by row - narrow the date range to see it all.
AUDIT_REPORT_MAX_RECORDS = 5000

_DISPLAY_DATE = "%d %b %Y"


def _choice_counts(qs, field: str, choices) -> list[dict]:
    """Zero-filled counts per choice, in the choice declaration order."""
    counted = dict(
        qs.values_list(field).annotate(n=Count("id")).order_by()
    )
    return [
        {"value": choice.value, "label": choice.label, "count": counted.get(choice.value, 0)}
        for choice in choices
    ]


def _period_label(filters: dict) -> str:
    date_from, date_to = filters.get("date_from"), filters.get("date_to")
    if date_from and date_to:
        return f"{date_from.strftime(_DISPLAY_DATE)} - {date_to.strftime(_DISPLAY_DATE)}"
    if date_from:
        return f"From {date_from.strftime(_DISPLAY_DATE)}"
    if date_to:
        return f"Up to {date_to.strftime(_DISPLAY_DATE)}"
    return "All time"


def applied_filters_labels(filters: dict) -> dict:
    """
    The report's filters as display strings, used verbatim by the PDF meta
    block, the Excel summary sheet and anyone rendering the JSON.
    """
    def joined(values, empty):
        return ", ".join(values) if values else empty

    actor_bits = [bit for bit in (
        filters.get("actor"),
        f"id {filters['actor_id']}" if filters.get("actor_id") else None,
    ) if bit]

    entity = joined(filters.get("entity_types"), "All entities")
    if filters.get("entity_id"):
        entity += f" #{filters['entity_id']}"

    return {
        "period": _period_label(filters),
        "systems": joined(filters.get("systems"), "All systems"),
        "actions": joined(filters.get("actions"), "All actions"),
        "actor_types": joined(filters.get("actor_types"), "All actor types"),
        "actor": joined(actor_bits, "All actors") if actor_bits else "All actors",
        "entity": entity,
    }


def _top_actors(qs, limit: int = 10) -> list[dict]:
    rows = (
        qs.exclude(actor_id=None)
        .values("actor_id", "actor_type", "actor_username",
                "actor_first_name", "actor_last_name")
        .annotate(entry_count=Count("id"))
        .order_by("-entry_count", "actor_id")[:limit]
    )
    return [
        {
            "actor_id": row["actor_id"],
            "actor_type": row["actor_type"],
            "actor_username": row["actor_username"],
            "actor_full_name": " ".join(
                part for part in (
                    (row["actor_first_name"] or "").strip(),
                    (row["actor_last_name"] or "").strip(),
                ) if part
            ) or None,
            "entry_count": row["entry_count"],
        }
        for row in rows
    ]


def _records(qs) -> list[dict]:
    records = []
    for log in qs[:AUDIT_REPORT_MAX_RECORDS]:
        full_name = " ".join(
            part for part in (
                (log.actor_first_name or "").strip(),
                (log.actor_last_name or "").strip(),
            ) if part
        ) or None
        local_created = timezone.localtime(log.created_at)
        records.append(
            {
                "id": log.id,
                "created_at": local_created.isoformat(),
                # Preformatted for the PDF/Excel, where an ISO "T" reads badly.
                "created_at_display": local_created.strftime("%Y-%m-%d %H:%M"),
                "system": log.system,
                "entity_type": log.entity_type,
                "entity_id": log.entity_id,
                "action": log.action,
                "actor_type": log.actor_type,
                "actor_id": log.actor_id,
                "actor_username": log.actor_username,
                "actor_full_name": full_name,
                "old_values": log.old_values,
                "new_values": log.new_values,
            }
        )
    return records


def build_audit_log_report(filters: dict) -> dict:
    """
    filters: the dict `api.audits.filters.parse_audit_filters` returns.
    Aggregates always cover the FULL filtered set; only `records` is capped.
    """
    qs = AuditLogService.get_queryset(filters)  # newest first, actor-annotated

    total = qs.count()
    first_last = qs.aggregate(first=Min("created_at"), last=Max("created_at"))

    daily = [
        {"date": row["day"].isoformat(), "count": row["n"]}
        for row in qs.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(n=Count("id"))
        .order_by("day")
    ]

    by_entity_type = [
        {"entity_type": row["entity_type"], "count": row["n"]}
        for row in qs.values("entity_type")
        .annotate(n=Count("id"))
        .order_by("-n", "entity_type")
    ]

    records = _records(qs)

    def _local_iso(value):
        return timezone.localtime(value).isoformat() if value else None

    return {
        "generated_at": timezone.localtime().isoformat(),
        "applied_filters": applied_filters_labels(filters),
        "summary": {
            "total_entries": total,
            "first_entry_at": _local_iso(first_last["first"]),
            "last_entry_at": _local_iso(first_last["last"]),
            "records_returned": len(records),
            "records_truncated": total > len(records),
        },
        "by_action": _choice_counts(qs, "action", AuditLog.Action),
        "by_system": _choice_counts(qs, "system", AuditLog.System),
        "by_actor_type": _choice_counts(qs, "actor_type", AuditLog.ActorTypeChoices),
        "by_entity_type": by_entity_type,
        "daily": daily,
        "top_actors": _top_actors(qs),
        "records": records,
    }
