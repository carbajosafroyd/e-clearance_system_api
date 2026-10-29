import logging
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import OuterRef, Q, QuerySet, Subquery

from .filters import actor_ids_matching
from .models import AuditLog
from .utils import diff_snapshots, resolve_actor, snapshot_instance

logger = logging.getLogger(__name__)


class AuditLogService:
    """
    Repository/service layer for AuditLog.
    Views must not contain query or business logic directly - it lives here.
    """

    @staticmethod
    def log_action(
        *,
        system: str,
        entity_type: str,
        entity_id,
        action: str,
        actor_type: str,
        actor_id=None,
        old_values: dict | None = None,
        new_values: dict | None = None,
    ) -> AuditLog:
        """
        Internal entry point other modules call to write an audit record.
        NOTE: this is intentionally not exposed as a public write endpoint -
        audit logs are immutable system records, not user-editable resources.
        """
        logger.info(
            "Creating audit log system=%s entity_type=%s entity_id=%s action=%s actor_type=%s actor_id=%s",
            system, entity_type, entity_id, action, actor_type, actor_id,
        )
        try:
            audit_log = AuditLog.objects.create(
                system=system,
                entity_type=entity_type,
                entity_id=str(entity_id),
                action=action,
                actor_type=actor_type,
                actor_id=actor_id,
                old_values=old_values,
                new_values=new_values,
            )
            return audit_log
        except Exception:
            logger.exception("Failed to create audit log for %s#%s", entity_type, entity_id)
            raise

    @classmethod
    def log_model_update(
        cls,
        *,
        instance,
        old_snapshot: dict,
        user,
        system: str,
        action: str = AuditLog.Action.UPDATE,
        extra_new: dict | None = None,
    ) -> AuditLog | None:
        """
        Diff `old_snapshot` (taken via utils.snapshot_instance BEFORE saving)
        against the instance's current state and record only what changed.

        Swallows its own failures: an audit write must never turn a
        successful business operation into a 500.
        """
        try:
            new_snapshot = snapshot_instance(instance)
            old_changed, new_changed = diff_snapshots(old_snapshot, new_snapshot)
            if not new_changed and not extra_new:
                logger.debug(
                    "Skipping audit log for %s#%s - nothing changed",
                    type(instance).__name__, instance.pk,
                )
                return None
            actor_type, actor_id = resolve_actor(user)
            return cls.log_action(
                system=system,
                entity_type=type(instance).__name__,
                entity_id=instance.pk,
                action=action,
                actor_type=actor_type,
                actor_id=actor_id,
                old_values=old_changed,
                new_values={**new_changed, **(extra_new or {})},
            )
        except Exception:
            logger.exception(
                "Audit update log failed for %s#%s",
                type(instance).__name__, getattr(instance, "pk", None),
            )
            return None

    @classmethod
    def log_model_delete(
        cls,
        *,
        entity_type: str,
        entity_id,
        snapshot: dict,
        user,
        system: str,
        extra_old: dict | None = None,
    ) -> AuditLog | None:
        """
        Record a delete. `snapshot` and `entity_id` must be captured BEFORE
        calling instance.delete() (Django nulls the pk afterwards).

        Swallows its own failures for the same reason as log_model_update.
        """
        try:
            actor_type, actor_id = resolve_actor(user)
            return cls.log_action(
                system=system,
                entity_type=entity_type,
                entity_id=entity_id,
                action=AuditLog.Action.DELETE,
                actor_type=actor_type,
                actor_id=actor_id,
                old_values={**snapshot, **(extra_old or {})},
                new_values=None,
            )
        except Exception:
            logger.exception("Audit delete log failed for %s#%s", entity_type, entity_id)
            return None

    @staticmethod
    def with_actor_names(qs: QuerySet) -> QuerySet:
        """
        Annotate each row with the acting user's username / first name /
        last name. AuditLog stores only a loose actor_id (no FK, so logs
        survive user deletion); a subquery per name field resolves it
        without an N+1 and reads as NULL when the user is gone.
        """
        user = get_user_model().objects.filter(id=OuterRef("actor_id"))
        return qs.annotate(
            actor_username=Subquery(user.values("username")[:1]),
            actor_first_name=Subquery(user.values("first_name")[:1]),
            actor_last_name=Subquery(user.values("last_name")[:1]),
        )

    @classmethod
    def get_queryset(cls, filters: dict) -> QuerySet:
        """
        Build a filtered, ordered, actor-annotated queryset from the
        normalised filters `api.audits.filters.parse_audit_filters` returns.

        List-valued keys (systems, entity_types, actions, actor_types) OR
        their values together; `actor` matches the users table by username /
        first / last name and narrows to those users' rows.
        """
        qs = AuditLog.objects.all()

        systems = filters.get("systems")
        if systems:
            qs = qs.filter(system__in=systems)

        entity_types = filters.get("entity_types")
        if entity_types:
            entity_q = Q()
            for entity_type in entity_types:
                entity_q |= Q(entity_type__iexact=entity_type)
            qs = qs.filter(entity_q)

        entity_id = filters.get("entity_id")
        if entity_id:
            qs = qs.filter(entity_id=entity_id)

        actor_types = filters.get("actor_types")
        if actor_types:
            qs = qs.filter(actor_type__in=actor_types)

        actor_id = filters.get("actor_id")
        if actor_id:
            qs = qs.filter(actor_id=actor_id)

        actor = filters.get("actor")
        if actor:
            qs = qs.filter(actor_id__in=actor_ids_matching(actor))

        actions = filters.get("actions")
        if actions:
            qs = qs.filter(action__in=actions)

        date_from = filters.get("date_from")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        date_to = filters.get("date_to")
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        logger.debug("AuditLog queryset built with filters=%s", filters)
        return cls.with_actor_names(qs).order_by("-created_at")

    @classmethod
    def get_by_id(cls, audit_log_id: int) -> AuditLog | None:
        cache_key = f"auditlog_{audit_log_id}"
        cached = cache.get(cache_key)
        if cached:
            logger.debug("Cache hit for %s", cache_key)
            return cached

        try:
            # Annotated so the serializers' actor-name fields resolve without
            # a second query; the annotations survive the cache round-trip.
            audit_log = cls.with_actor_names(AuditLog.objects.all()).get(
                id=audit_log_id
            )
        except AuditLog.DoesNotExist:
            logger.warning("AuditLog id=%s not found", audit_log_id)
            return None

        cache.set(cache_key, audit_log, 60 * 60 * 24)
        return audit_log
