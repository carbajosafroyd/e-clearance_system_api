import hashlib
import logging

from django.core.cache import cache
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.rbac import DEFAULT, DENY, ActionPermission
from api.utils import custom_paginations

from .filters import parse_audit_filters
from .models import AuditLog
from .serializers import AuditLogReadDetailedSerializer, AuditLogReadSlimSerializer
from .services import AuditLogService

logger = logging.getLogger(__name__)

MODEL_NAME = "AUDITLOG"

_MULTI_HINT = (
    "Accepts multiple values: repeat the parameter "
    "(`?{name}=a&{name}=b`) or send them comma-separated (`?{name}=a,b`). "
    "Values are OR-ed together; unknown values are rejected with a 400."
)


def _multi_choice_param(name: str, choices, description: str) -> OpenApiParameter:
    """A documented multi-value enum query parameter."""
    return OpenApiParameter(
        name=name,
        type={"type": "array", "items": {"type": "string", "enum": [c.value for c in choices]}},
        style="form",
        explode=True,
        description=f"{description} {_MULTI_HINT.format(name=name)}",
    )


# Shared by the list endpoint and the audit-log report endpoints - one filter
# vocabulary for both, documented once.
AUDIT_FILTER_PARAMS = [
    _multi_choice_param(
        "system", AuditLog.System, "Originating system of the entries."
    ),
    _multi_choice_param("action", AuditLog.Action, "What was done to the entity."),
    _multi_choice_param(
        "actor_type", AuditLog.ActorTypeChoices, "What kind of account acted."
    ),
    OpenApiParameter(
        "entity_type",
        {"type": "array", "items": {"type": "string"}},
        style="form",
        explode=True,
        description=(
            "Model name(s) the entry is about, matched case-insensitively - "
            "e.g. `Payment`, `Fee`, `GeneratedFeeBatch`, `FeeExemptedStudent`. "
            + _MULTI_HINT.format(name="entity_type")
        ),
        examples=[OpenApiExample("Payments and fees", value="Payment,Fee")],
    ),
    OpenApiParameter(
        "entity_id",
        str,
        description="Exact primary key of the entity the entry is about.",
    ),
    OpenApiParameter(
        "actor",
        str,
        description=(
            "Search the acting user by name: matches username, first name and "
            "last name, case-insensitively. Multiple words must all match "
            '(each against any of the three fields), so `actor=juan cruz` '
            "finds Juan Cruz. Use this instead of guessing `actor_id`."
        ),
    ),
    OpenApiParameter(
        "actor_id",
        int,
        description=(
            "Exact id of the acting user, when it is already known (e.g. "
            "drilling into one user's trail from another screen)."
        ),
    ),
    OpenApiParameter(
        "date_from",
        str,
        description="Only entries created on or after this date (YYYY-MM-DD).",
    ),
    OpenApiParameter(
        "date_to",
        str,
        description="Only entries created on or before this date (YYYY-MM-DD).",
    ),
]

LIST_PARAMS = AUDIT_FILTER_PARAMS + [
    OpenApiParameter("current_page", int, description="Page number (project pagination convention)."),
    OpenApiParameter("per_page", int, description="Page size (project pagination convention)."),
]


@extend_schema(tags=["Audit Log"])
class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only by design: audit records are immutable and are written
    exclusively via AuditLogService.log_action() from other modules,
    never through this API surface.
    """
    queryset = AuditLogService.get_queryset({})
    pagination_class = custom_paginations.CustomPagination
    permission_classes = [IsAuthenticated, ActionPermission]
    # Audit logs are sensitive: students must never reach them. The trail is
    # read-only here - it is written only by AuditLogService - so everything
    # that is not a read is denied outright.
    required_permissions = {
        "list": "audits.view_all_audit_logs",
        "retrieve": "audits.view_all_audit_logs",
        DEFAULT: DENY,
    }

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AuditLogReadDetailedSerializer
        return AuditLogReadSlimSerializer

    @extend_schema(
        summary="List audit log entries",
        parameters=LIST_PARAMS,
        responses=AuditLogReadSlimSerializer(many=True),
        description=(
            "One row per recorded change, newest first, with the acting "
            "user's username and full name resolved from the users table "
            "(`actor_username` / `actor_full_name`, null for system entries "
            "or deleted users).\n\n"
            "All filters combine with AND; the multi-value ones (`system`, "
            "`action`, `actor_type`, `entity_type`) OR their own values "
            "together, so `?action=create,delete&system=collection` reads as "
            "*(create OR delete) AND collection*. Responses are cached per "
            "query-string until the next audit write.\n\n"
            "For aggregate views of the same data (counts by action/system/"
            "actor, daily activity, PDF/Excel exports) use "
            "`/audit-logs/report/` and its `/pdf/` and `/excel/` variants, "
            "which take exactly the same filter parameters."
        ),
    )
    def list(self, request, *args, **kwargs):
        filters = parse_audit_filters(request.query_params)

        version = cache.get(f"{MODEL_NAME}_list_version", "1")
        # The key hashes the WHOLE query string (filters included), not just
        # the pagination params - otherwise two different filter combinations
        # on the same page would serve each other's cached payload.
        query_signature = hashlib.md5(
            "&".join(
                f"{key}={value}"
                for key in sorted(request.query_params.keys())
                for value in request.query_params.getlist(key)
            ).encode()
        ).hexdigest()
        cache_key = f"{MODEL_NAME}_list_{query_signature}_v{version}"

        cached_response = cache.get(cache_key)
        if cached_response is not None:
            logger.info("Cache hit for %s", cache_key)
            return Response(cached_response)

        logger.info("Cache miss for %s - querying DB", cache_key)
        self.queryset = AuditLogService.get_queryset(filters)

        response = super().list(request, *args, **kwargs)
        cache.set(cache_key, response.data, 60 * 60 * 24)
        return response

    @extend_schema(
        summary="Retrieve one audit log entry",
        responses=AuditLogReadDetailedSerializer,
        description=(
            "The full record: the changed-field diff (`old_values` / "
            "`new_values`) plus the resolved actor identity (username, first "
            "and last name). Cached for a day per id."
        ),
    )
    def retrieve(self, request, *args, **kwargs):
        audit_log_id = kwargs.get("pk")
        audit_log = AuditLogService.get_by_id(audit_log_id)
        if audit_log is None:
            logger.warning("AuditLog id=%s not found on retrieve", audit_log_id)
            return Response({"detail": "Not found."}, status=404)
        serializer = self.get_serializer(audit_log)
        return Response(serializer.data)
