from rest_framework import serializers
from api.notifications.models import NotificationBroadcast


class NotificationBroadcastReadSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.get_full_name", read_only=True)
    institute_name = serializers.CharField(
        source="target_institute.name", read_only=True
    )
    program_name = serializers.CharField(source="target_program.name", read_only=True)

    class Meta:
        model = NotificationBroadcast
        fields = [
            "id",
            "title",
            "message",
            "sender_name",
            "institute_name",
            "program_name",
            "target_year_level",
            "total_recipients",
            "created_at",
        ]
