from django.contrib.auth import get_user_model
from rest_framework import serializers
from api.notifications.models import Notification

User = get_user_model()


class NotificationSenderSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "name"]

    def get_name(self, obj) -> str:
        # Fallback to username/email if full name elements are missing
        full_name = obj.get_full_name().strip()
        return full_name if full_name else obj.username


class NotificationDetailSerializer(serializers.ModelSerializer):
    sender = NotificationSenderSerializer(read_only=True)
    # Explicitly map data field to handle JSON variations gracefully
    data = serializers.JSONField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "message",
            "type",
            "source",
            "data",
            "sender",
            "is_read",
            "created_at",
        ]
        read_only_fields = fields
