from rest_framework import serializers
from api.notifications.models import Notification

class NotificationUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["is_read"]
        
    def validate_is_read(self, value):
        if not isinstance(value, bool):
            raise serializers.ValidationError("is_read must be a boolean value.")
        return value