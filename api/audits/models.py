import time
import logging

from django.core.cache import cache
from django.db import models

logger = logging.getLogger(__name__)


class AuditLog(models.Model):
    class ActorTypeChoices(models.TextChoices):
        STUDENT = "student", "Student"
        STAFF = "staff", "Staff"
        SYSTEM = "system", "System"
        ADMIN = "admin", "Admin"

    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        CANCEL = "cancel", "Cancel"
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"

    class System(models.TextChoices):
        COLLECTION = "collection", "Collection"
        LOCKER = "locker", "Locker"
        ATTENDANCE = "attendance", "Attendance"
        VOTING = "voting", "Voting"
        CLEARANCE = "clearance", "Clearance"
        # Cross-system administration: role assignments, user invitations,
        # platform settings. Not owned by any one student-facing system.
        PLATFORM = "platform", "Platform"

    # Which system the log originated from, so consumers can query one
    # system's trail without knowing every entity_type it owns.
    system = models.CharField(max_length=20, choices=System.choices)

    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=50)

    action = models.CharField(max_length=20, choices=Action.choices)

    actor_type = models.CharField(
        max_length=20,
        choices=ActorTypeChoices.choices,
    )
    actor_id = models.IntegerField(null=True, blank=True)

    old_values = models.JSONField(null=True, blank=True)
    new_values = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audits"
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["actor_type", "actor_id"]),
            models.Index(fields=["created_at"]),
            # Explicit name so the hand-written 0002 migration matches.
            models.Index(fields=["system", "created_at"], name="audits_system_created_idx"),
        ]
        permissions = [
            ("view_all_audit_logs", "Can view audit logs across all entities"),
            ("export_audit_logs", "Can export the audit trail to PDF/Excel"),
        ]

    def __str__(self):
        return f"{self.action} on {self.entity_type}#{self.entity_id} by {self.actor_type}:{self.actor_id}"

    def save(self, *args, **kwargs):
        """
        Cache versioning strategy (per project convention):
        on every write -> invalidate the specific record cache key,
        invalidate the dashboard aggregate, and bump the list version
        so paginated list caches are transparently invalidated.
        """
        super().save(*args, **kwargs)
        logger.info("AuditLog saved id=%s entity_type=%s action=%s", self.id, self.entity_type, self.action)
        self._invalidate_cache()

    def delete(self, *args, **kwargs):
        record_id = self.id
        super().delete(*args, **kwargs)
        logger.info("AuditLog deleted id=%s", record_id)
        cache.delete(f"auditlog_{record_id}")
        cache.delete("dashboard_total")
        cache.set("AUDITLOG_list_version", str(int(time.time())), None)

    def _invalidate_cache(self):
        cache.delete(f"auditlog_{self.id}")
        cache.delete("dashboard_total")
        cache.set("AUDITLOG_list_version", str(int(time.time())), None)
