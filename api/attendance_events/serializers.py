from rest_framework import serializers
from api.attendance_events.models import AttendanceEvent


class AttendanceEventReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceEvent
        fields = "__all__"


class AttendanceEventReadSlimSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceEvent
        fields = ["id", "event_name", "description"]


class AttendanceEventCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceEvent
        fields = ["event_name", "description"]


class AttendanceEventUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceEvent
        fields = ["event_name", "description"]


class AttendanceEventDeleteSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceEvent
        fields = ["id"]
