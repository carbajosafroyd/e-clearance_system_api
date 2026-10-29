from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import AuditLog


class ActorNameFieldsMixin(serializers.Serializer):
    """
    Human-readable actor columns resolved from the users table.

    `AuditLogService.with_actor_names()` annotates these onto every queryset
    the endpoints serve, so reading them is free. The fallback lookup only
    fires for instances that predate the annotation (e.g. a retrieve served
    from a cache entry written before this feature shipped) and costs one
    query for that single object. All of them are null when the actor was
    the system (no actor_id) or the user has since been deleted.
    """

    actor_username = serializers.SerializerMethodField()
    actor_first_name = serializers.SerializerMethodField()
    actor_last_name = serializers.SerializerMethodField()
    actor_full_name = serializers.SerializerMethodField()

    def _actor_attr(self, obj, annotation: str, user_field: str):
        if hasattr(obj, annotation):
            return getattr(obj, annotation)
        if obj.actor_id is None:
            return None
        if not hasattr(obj, "_actor_user"):
            obj._actor_user = (
                get_user_model().objects.filter(id=obj.actor_id).first()
            )
        return getattr(obj._actor_user, user_field, None)

    def get_actor_username(self, obj) -> str | None:
        return self._actor_attr(obj, "actor_username", "username")

    def get_actor_first_name(self, obj) -> str | None:
        return self._actor_attr(obj, "actor_first_name", "first_name")

    def get_actor_last_name(self, obj) -> str | None:
        return self._actor_attr(obj, "actor_last_name", "last_name")

    def get_actor_full_name(self, obj) -> str | None:
        parts = [
            self.get_actor_first_name(obj) or "",
            self.get_actor_last_name(obj) or "",
        ]
        return " ".join(part for part in parts if part.strip()) or None


class AuditLogReadSlimSerializer(ActorNameFieldsMixin, serializers.ModelSerializer):
    """Minimal fields for list endpoint - keeps cached/paginated payloads light."""

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "system",
            "entity_type",
            "entity_id",
            "action",
            "actor_type",
            "actor_id",
            "actor_username",
            "actor_full_name",
            "created_at",
        ]
        read_only_fields = fields


class AuditLogReadDetailedSerializer(ActorNameFieldsMixin, serializers.ModelSerializer):
    """Full record including diff payload and actor names, used for retrieve."""

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "system",
            "entity_type",
            "entity_id",
            "action",
            "actor_type",
            "actor_id",
            "actor_username",
            "actor_first_name",
            "actor_last_name",
            "actor_full_name",
            "old_values",
            "new_values",
            "created_at",
        ]
        read_only_fields = fields


class AuditLogWriteSerializer(serializers.ModelSerializer):
    """
    Used internally by AuditLogService.log_action(), NOT wired to a public
    create/update endpoint. Validation lives here so any internal caller
    (e.g. from other apps' services) gets the same guarantees.
    """

    class Meta:
        model = AuditLog
        fields = [
            "system",
            "entity_type",
            "entity_id",
            "action",
            "actor_type",
            "actor_id",
            "old_values",
            "new_values",
        ]

    def validate_entity_id(self, value):
        if not str(value).strip():
            raise serializers.ValidationError("entity_id cannot be blank.")
        return str(value)

    def validate(self, attrs):
        if attrs.get("action") == AuditLog.Action.UPDATE and not attrs.get("new_values"):
            raise serializers.ValidationError(
                {"new_values": "new_values is required for update actions."}
            )
        return attrs
