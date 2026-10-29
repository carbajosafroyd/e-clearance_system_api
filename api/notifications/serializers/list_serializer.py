from rest_framework import serializers
from api.notifications.models import Notification

class NotificationListSerializer(serializers.ModelSerializer):
    message_preview = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "type",
            "source",
            "is_read",
            "created_at",
            "message_preview",
        ]
        read_only_fields = fields

    def get_message_preview(self, obj) -> str:
        """Truncates the full message to provide a lightweight preview string."""
        if not obj.message:
            return ""
        max_length = 80
        if len(obj.message) > max_length:
            return f"{obj.message[:max_length].rstrip()}..."
        return obj.message