from rest_framework import serializers

from api.institutes.serializers import InstituteReadSlimSerializer
from .models import InstituteAttendanceEvent
from ..attendance_events.serializers import AttendanceEventReadSlimSerializer
from api.institutes_attendance_event_settings.serializers import (
    InstituteAttendanceEventSettingReadMiniSerializer,
)


class InstituteAttendanceEventReadSerializer(serializers.ModelSerializer):
    institute = InstituteReadSlimSerializer()
    attendance_event = AttendanceEventReadSlimSerializer()
    event_settings = InstituteAttendanceEventSettingReadMiniSerializer(
        source="settings", many=True, read_only=True
    )

    class Meta:
        model = InstituteAttendanceEvent
        fields = "__all__"


class InstituteAttendanceEventReadSlimSerializer(serializers.ModelSerializer):
    # institute = InstituteReadSlimSerializer()
    attendance_event = AttendanceEventReadSlimSerializer()

    class Meta:
        model = InstituteAttendanceEvent
        fields = [
            "id",
            # "institute",
            "attendance_event",
            "academic_year",
            "semester",
            "start_date",
            "end_date",
            "event_status",
        ]


class InstituteAttendanceEventReadMiniSerializer(serializers.ModelSerializer):
    # institute_name = serializers.CharField(source="institute.institute_name")
    attendance_event = AttendanceEventReadSlimSerializer()

    class Meta:
        model = InstituteAttendanceEvent
        fields = [
            "id",
            # "institute_name",
            "attendance_event",
            "academic_year",
            "semester",
            "start_date",
            "end_date",
            "event_status",
        ]


class InstituteAttendanceEventCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstituteAttendanceEvent
        fields = [
            # "institute",
            "attendance_event",
            "academic_year",
            "semester",
            "start_date",
            "end_date",
            # "event_status",
        ]


class InstituteAttendanceEventUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstituteAttendanceEvent
        fields = [
            # "institute",
            # "attendance_event",
            # "academic_year",
            # "semester",
            "start_date",
            "end_date",
            "event_status",
        ]


class InstituteAttendanceEventDeleteSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstituteAttendanceEvent
        fields = ["id"]


# class AttendanceEventCreateSerializer(serializers.ModelSerializer):
#     pass
