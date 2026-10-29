from rest_framework import serializers
from api.notifications.models import Notification, NotificationBroadcast
from api.notifications.service import create_targeted_broadcast
from api.programs.models import Program


class NotificationBroadcastSerializer(serializers.ModelSerializer):
    institute = serializers.BooleanField(
        required=False,
        write_only=True,
        default=True,
    )
    program = serializers.PrimaryKeyRelatedField(
        queryset=Program.objects.all(),
        required=False,
        allow_null=True,
        write_only=True,
        default=None,
    )
    year_level = serializers.IntegerField(
        source="target_year_level", required=False, allow_null=True, default=None
    )
    type = serializers.CharField(
        write_only=True, required=False, default="system.announcement"
    )

    class Meta:
        model = NotificationBroadcast

        fields = [
            "id",
            "title",
            "message",
            "type",
            "institute",
            "program",
            "year_level",
            "total_recipients",
            "created_at",
        ]
        read_only_fields = ["id", "total_recipients", "created_at"]

    def validate_year_level(self, value):
        if value is not None and (value < 1 or value > 4):
            raise serializers.ValidationError("Year level must be between 1 and 4.")
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        sender = request.user if request else None

        target_institute = attrs.get("institute")
        target_program = attrs.get("program")

        if sender:
            if target_institute and not sender.institute:
                raise serializers.ValidationError(
                    {"institute": "Sender must belong to a Institute."}
                )

            if target_program and target_program.institute != sender.institute:
                raise serializers.ValidationError(
                    {"program": "Selected program does not belong to your Institute."}
                )

        return super().validate(attrs)

    def create(self, validated_data):
        institute_flag = validated_data.pop("institute", True)
        program_instance = validated_data.pop("program", None)
        notification_type = validated_data.pop("type", "system.announcement")

        request = self.context.get("request")
        sender = request.user if request else None

        actual_institute = None
        if institute_flag and sender and hasattr(sender, "institute"):
            actual_institute = sender.institute

        return create_targeted_broadcast(
            sender=sender, # type: ignore
            title=validated_data.get("title"),
            message=validated_data.get("message"),
            notification_type=notification_type,
            institute=actual_institute,  
            program_id=program_instance.id if program_instance else None,
            year_level=validated_data.get("target_year_level"),
        )



class MarkFeesOverdueResponseSerializer(serializers.Serializer):
    updated_count = serializers.IntegerField()
    fee_ids = serializers.ListField(child=serializers.IntegerField())


class OverdueBroadcastResponseSerializer(serializers.Serializer):
    #: Fees flipped PENDING -> OVERDUE by this call, before anyone was told.
    #: The recipient count answers a different question: it covers everybody
    #: currently overdue, including those who already were.
    updated_count = serializers.IntegerField(default=0)
    broadcast_id = serializers.IntegerField()
    title = serializers.CharField()
    message = serializers.CharField()
    total_recipients = serializers.IntegerField()
    notified_user_ids = serializers.ListField(child=serializers.IntegerField())