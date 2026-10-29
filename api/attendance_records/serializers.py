from rest_framework import serializers
from api.attendance_records.models import AttendanceRecord
from api.students import serializers as StudentSerializers
from api.institutes_attendance_events import (
    serializers as InstituteAttendanceEventSerializer,
)
from api.fees import serializers as FeeSerializer
from api.students.models import Student
from django.db.models import Q
from api.institutes_attendance_events.models import InstituteAttendanceEvent


class AttendanceRecordReadSerializer(serializers.ModelSerializer):

    student = StudentSerializers.StudentReadSlimSerializer()
    institute_attendance_event = (
        InstituteAttendanceEventSerializer.InstituteAttendanceEventReadSlimSerializer()
    )
    fee = FeeSerializer.FeeReadMiniSerializer()

    class Meta:
        model = AttendanceRecord
        fields = "__all__"


class AttendanceRecordReadSlimSerializer(serializers.ModelSerializer):

    student = StudentSerializers.StudentReadMiniSerializer()
    institute_attendance_event =  InstituteAttendanceEventSerializer.InstituteAttendanceEventReadMiniSerializer()
    
    fee = FeeSerializer.FeeReadMiniSerializer()

    class Meta:
        model = AttendanceRecord
        fields = "__all__"


class AttendanceRecordCreateSerializer(serializers.ModelSerializer):
    student_school_id_or_rfid = serializers.CharField(source="student", write_only=True)
    student = StudentSerializers.StudentReadMiniSerializer(read_only=True)
    institute_attendance_event = serializers.PrimaryKeyRelatedField(
        queryset=InstituteAttendanceEvent.objects.all()
    )

    class Meta:
        model = AttendanceRecord
        fields = [
            "student_school_id_or_rfid",
            "student",
            "institute_attendance_event",
            "morning_check_in",
            "morning_check_out",
            "afternoon_check_in",
            "afternoon_check_out",
            # "total_fines",
            # "fee",,
            "created_at",# for imports
            "date",
        ]
        validators = [
            serializers.UniqueTogetherValidator(
                queryset=AttendanceRecord.objects.all(),
                fields=["student", "institute_attendance_event", "date"],
                message="Attendance for this student in this event on this date already exists.",
            )
        ]

    # def validate_institute_attendance_event(self, value):
    #     request = self.context["request"]
    #     user_institute = request.user.institute

    #     if value.institute != user_institute:
    #         raise serializers.ValidationError(
    #             "Institute event not found or is outside assigned institute."
    #         )
    #     return value


    def validate_student_school_id_or_rfid(self, value):
        try:
            student = Student.objects.get(Q(s_studentID=value) | Q(s_rfid=value))

            if not student.program.institute == self.context["request"].user.institute:
                raise serializers.ValidationError(
                    "Student not found or is outside assigned institute"
                )
        except Student.DoesNotExist:
            raise serializers.ValidationError(
                "Student not found with this School ID or RFID"
            )
        except Student.MultipleObjectsReturned:
            raise serializers.ValidationError(
                "Multiple students found with this ID or RFID"
            )
        return student

    def validate(self, attrs):
        date = attrs["date"]
        event = attrs["institute_attendance_event"]

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
        return attrs
    
    def create(self, validated_data):
        validated_data["updated_by"] = self.context["request"].user
        return validated_data


class AttendanceRecordUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = [
            "student",
            "institute_attendance_event",
            "morning_check_in",
            "morning_check_out",
            "afternoon_check_in",
            "afternoon_check_out",
            "total_fines",
            "fee",
            "date",
        ]


class AttendanceRecordDeleteSerializer(serializers.ModelSerializer):
    class Meta:
        model = AttendanceRecord
        fields = ["id"]


class AttendanceRecordsImportFileSerializer(serializers.ModelSerializer):
    file = serializers.FileField()
    
    class Meta:
        model = AttendanceRecord
        fields = ["file", "institute_attendance_event"]

    def validate(self, attrs):
        institute_attendance_event = attrs["institute_attendance_event"]

        if (
            not institute_attendance_event.institute
            == self.context["request"].user.institute
        ):
            raise serializers.ValidationError(
                "Not allowed to import Attendance Records Outside Assigned Institute"
            )

        return attrs


class AttendanceRecordsBulkDeleteeSerializer(serializers.Serializer):
    institute_attendance_event = serializers.IntegerField()