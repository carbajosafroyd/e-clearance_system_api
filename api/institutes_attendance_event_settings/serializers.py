from rest_framework import serializers
from api.institutes_attendance_event_settings.models import (
    InstituteAttendanceEventSetting,
)
from typing import Any
# from api.institutes_attendance_events.serializers import (
#     InstituteAttendanceEventReadSerializer,
#     InstituteAttendanceEventReadSlimSerializer,
# )


class InstituteAttendanceEventSettingReadSerializer(serializers.ModelSerializer):
    # institute_attendance_event = InstituteAttendanceEventReadSerializer()
    institute_attendance_event = serializers.SerializerMethodField()

    class Meta:
        model = InstituteAttendanceEventSetting
        fields = "__all__"

    def get_institute_attendance_event(self, obj) -> Any:
        # Local import here breaks the circle   
        from api.institutes_attendance_events.serializers import (
            InstituteAttendanceEventReadMiniSerializer,
        )

        serializer = InstituteAttendanceEventReadMiniSerializer(
            obj.institute_attendance_event
        )
        return serializer.data


class InstituteAttendanceEventSettingReadSlimSerializer(serializers.ModelSerializer):
    institute_attendance_event_id = serializers.CharField(
        source="institute_attendance_event.id"
    )
    institute_attendance_event_name = serializers.CharField(
        source="institute_attendance_event.attendance_event.event_name"
    )

    class Meta:
        model = InstituteAttendanceEventSetting
        fields = [
            "id",
            "institute_attendance_event_id",
            "institute_attendance_event_name",
            "date",
            "checkin_start",
            "checkin_end",
            "checkout_start",
            "checkout_end",
            "afternoon_checkin_start",
            "afternoon_checkin_end",
            "afternoon_checkout_start",
            "afternoon_checkout_end",
            "event_status",
        ]


class InstituteAttendanceEventSettingReadMiniSerializer(serializers.ModelSerializer):

    class Meta:
        model = InstituteAttendanceEventSetting
        fields = [
            "id",
            "date",
            "checkin_start",
            "checkin_end",
            "checkout_start",
            "checkout_end",
            "afternoon_checkin_start",
            "afternoon_checkin_end",
            "afternoon_checkout_start",
            "afternoon_checkout_end",
            "event_status",
        ]


class InstituteAttendanceEventSettingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstituteAttendanceEventSetting
        fields = [
            "institute_attendance_event",
            "date",
            "has_morning_in",
            "has_morning_out",
            "has_afternoon_in",
            "has_afternoon_out",
            "checkin_start",
            "checkin_end",
            "checkout_start",
            "checkout_end",
            "afternoon_checkin_start",
            "afternoon_checkin_end",
            "afternoon_checkout_start",
            "afternoon_checkout_end",
        ]

    ## wala pa kuni na implement sa erd huhu og serializers dapat  implement ni after naku e open
    def validate(self, attrs):
        event = attrs["institute_attendance_event"]
        date = attrs["date"]
        user = self.context["request"].user

        if event.institute != user.institute:
            raise serializers.ValidationError(
                "You cannot create event settings outside your assigned institute"
            )

        if not (event.start_date <= date <= event.end_date):
            raise serializers.ValidationError(
                {
                    "date": (
                        f"The selected date ({date}) is outside the allowed range "
                        f"for this attendance event. "
                        f"Valid dates are from {event.start_date} to {event.end_date}."
                    )
                }
            )

        if InstituteAttendanceEventSetting.objects.filter(
            institute_attendance_event=event, date=date
        ).exists():
            raise serializers.ValidationError(
                {"date": f"Attendance for '{event}' on {date} already exists."}
            )

        """
        Validate that:
        1. Required times exist if their corresponding has_* flag is True
        2. Start times are before end times
        """

        def validate_period(start_field, end_field, active_flag=None):
            start = attrs.get(start_field)
            end = attrs.get(end_field)
            active = True if active_flag is None else attrs.get(active_flag)

            # If the period is marked active, it must exist
            if active:
                if not start or not end:
                    raise serializers.ValidationError(
                        {
                            start_field: f"{start_field.replace('_',' ').capitalize()} is required because {active_flag.replace('_',' ')} is True",
                            end_field: f"{end_field.replace('_',' ').capitalize()} is required because {active_flag.replace('_',' ')} is True",
                        }
                    )
                # Validate start < end
                if start >= end:
                    raise serializers.ValidationError(
                        {
                            start_field: f"{start_field.replace('_',' ').capitalize()} must be before {end_field.replace('_',' ')}",
                            end_field: f"{end_field.replace('_',' ').capitalize()} must be after {start_field.replace('_',' ')}",
                        }
                    )

        validate_period("checkin_start", "checkin_end", active_flag="has_morning_in")
        validate_period("checkout_start", "checkout_end", active_flag="has_morning_out")
        validate_period(
            "afternoon_checkin_start",
            "afternoon_checkin_end",
            active_flag="has_afternoon_in",
        )
        validate_period(
            "afternoon_checkout_start",
            "afternoon_checkout_end",
            active_flag="has_afternoon_out",
        )

        return attrs


class InstituteAttendanceEventSettingUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstituteAttendanceEventSetting
        fields = [
            # "institute_attendance_event",
            "date",
            "checkin_start",
            "checkin_end",
            "checkout_start",
            "checkout_end",
            "afternoon_checkin_start",
            "afternoon_checkin_end",
            "afternoon_checkout_start",
            "afternoon_checkout_end",
            "event_status",
        ]


class InstituteAttendanceEventSettingDeleteSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstituteAttendanceEventSetting
        fields = ["id"]


# NOTE that we should test signal feature of django, so once the attendance event were created it will also create event settings that corresponds to that event :>
