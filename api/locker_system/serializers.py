from datetime import timezone
from rest_framework import serializers

from api.schools.serializers import SchoolBuildingReadSlimSerializer
from .models import Locker, LockerSubscription, LockerSubscriptionRequest, LockerReport

from api.schools.models import SchoolBuilding

from api.students.serializers import StudentReadSlimSerializer, StudentReadMiniSerializer
from api.institutes.serializers import InstituteReadSlimSerializer
from api.fees.serializers import FeeReadMiniSerializer

from rest_framework import serializers

from core.choices import Semester, AcademicYear, get_default_academic_year

class LockerQueryParamsSerializer(serializers.Serializer):
    # add pud gurog building here   
    building = serializers.PrimaryKeyRelatedField(queryset=SchoolBuilding.objects.all(),required=False)
    academic_year = serializers.ChoiceField(
        required=True,
        choices = AcademicYear.choices(),
        help_text="Academic year, e.g. 2025-2026",
    )
    semester = serializers.ChoiceField(
        choices=Semester.choices,
        required=True,
        help_text="First Second",
    )


class LockerMetricsSerializer(serializers.Serializer):
    """
    Response shape of /lockers/metrics/. Not bound to a model - the view
    assembles the numbers from counts, so this only describes them.
    """

    total_lockers = serializers.IntegerField()
    occupied_lockers = serializers.IntegerField()
    vacant_lockers = serializers.IntegerField()
    under_maintenance_lockers = serializers.IntegerField()
    total_student_renters = serializers.IntegerField()
    total_paid_renters = serializers.IntegerField()
    student_adoption_rate = serializers.FloatField(help_text="Percentage, 0-100.")
    locker_utilization_rate = serializers.FloatField(help_text="Percentage, 0-100.")
    remaining_capacity_locker = serializers.FloatField(help_text="Percentage, 0-100.")
    percentage_under_maintenance = serializers.FloatField(help_text="Percentage, 0-100.")
    percentage_paid_renters = serializers.FloatField(help_text="Percentage, 0-100.")


class LockerReadSerializer(serializers.ModelSerializer):
    building = SchoolBuildingReadSlimSerializer()
    institute = InstituteReadSlimSerializer()

    class Meta:
        model = Locker
        fields = "__all__"


class LockerReadSlimSerializer(serializers.ModelSerializer):
    building = SchoolBuildingReadSlimSerializer()
    institute = InstituteReadSlimSerializer()

    class Meta:
        model = Locker
        exclude = ["created_at", "updated_at", "updated_by"]


class LockerCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Locker
        fields = ["locker_number", "building", "floor"]


class LockerUpdateSeralizer(serializers.ModelSerializer):
    class Meta:
        model = Locker
        fields = ["locker_number", "building", "floor", "is_active"]


class LockerDeleteSeralizer(serializers.ModelSerializer):
    class Meta:
        model = Locker
        fields = ["id"]


class LockerBulkCreateSerializer(serializers.Serializer):
    building = serializers.PrimaryKeyRelatedField(queryset=SchoolBuilding.objects.all())
    floor = serializers.IntegerField(min_value=1)
    start_locker_number = serializers.IntegerField(min_value=1)
    end_locker_number = serializers.IntegerField(min_value=1)

    def validate(self, data):
        if data["start_locker_number"] > data["end_locker_number"]:
            raise serializers.ValidationError(
                "start_locker_number must be less than or equal to end_locker_number"
            )

        building = data["building"]

        if data["floor"] > building.max_floor:
            raise serializers.ValidationError(
                "floor must be less than or equal to maximum floor of the building"
            )

        return data


class LockerBulkDeleteSerializer(serializers.Serializer):
    building = serializers.PrimaryKeyRelatedField(queryset=SchoolBuilding.objects.all())
    floor = serializers.IntegerField(min_value=1)
    start_locker_number = serializers.IntegerField(min_value=1)
    end_locker_number = serializers.IntegerField(min_value=1)


# LOCKER REPORTS
class LockerReportCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LockerReport
        fields = ["locker", "message"]

    def validate(self, data):
        request = self.context.get("request")
        user = request.user


        if not hasattr(user, "student"):
            raise serializers.ValidationError(
                "Only students can create locker reports."
            )
        
        if not data.get("message").strip():
            raise serializers.ValidationError("Message cannot be empty")
        return data
    
    def create(self, validated_data):
        validated_data["student"] = self.context["request"].user.student
        return super().create(validated_data)

class LockerReportsReadSerializer(serializers.ModelSerializer):
    student = StudentReadSlimSerializer()
    locker = LockerReadSlimSerializer()

    class Meta:
        model = LockerReport
        fields = "__all__"

class LockerReportsReadSlimSerializer(serializers.ModelSerializer):
    student = StudentReadMiniSerializer()
    locker = LockerReadSlimSerializer()

    class Meta:
        model = LockerReport
        exclude = ["created_at", "updated_at", "updated_by"]



#  LOCKER SUBSCRIPTION REQUEST
class LockerSubscriptionRequestCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LockerSubscriptionRequest
        fields = ["locker", "academic_year", "semester"]

    def validate(self, data):
        request = self.context.get("request")
        user = request.user

        if not hasattr(user, "student"):
            raise serializers.ValidationError(
                "Only students can create locker subscription requests."
            )

        student = user.student

        exists = LockerSubscriptionRequest.objects.filter(
            student=student,
            locker=data["locker"],
            academic_year=data["academic_year"],
            semester=data["semester"],
        ).exists()

        if exists:
            raise serializers.ValidationError(
                "You already requested this locker for the selected academic year and semester."
            )

        return data

    def create(self, validated_data):
        validated_data["student"] = self.context["request"].user.student
        return super().create(validated_data)

class LockerSubscriptionRequestReadSerializer(serializers.ModelSerializer):
    student = StudentReadSlimSerializer()
    locker = LockerReadSlimSerializer()

    class Meta:
        model = LockerSubscriptionRequest
        fields = "__all__"

class LockerSubscriptionRequestReadSlimSerializer(serializers.ModelSerializer):
    student = StudentReadMiniSerializer()
    locker = LockerReadSlimSerializer()

    class Meta:
        model = LockerSubscriptionRequest
        exclude = ["created_at", "updated_at", "updated_by"]



class LockerSubscriptionRequestStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = LockerSubscriptionRequest
        fields = ["status"]

    def validate_status(self, value):
        if value not in [
            LockerSubscriptionRequest.SubscriptionStatus.APPROVED,
            LockerSubscriptionRequest.SubscriptionStatus.REJECTED,
            LockerSubscriptionRequest.SubscriptionStatus.CANCELLED,
        ]:
            raise serializers.ValidationError("Invalid status transition.")
        return value


# LOCKER SUBSCRIPTIONS
class LockerSubscriptionReadSerializer(serializers.ModelSerializer):
    student = StudentReadMiniSerializer()
    locker = LockerReadSlimSerializer()
    subscription_request = serializers.StringRelatedField()
    created_by = serializers.StringRelatedField()
    cancelled_by = serializers.StringRelatedField()
    fee = FeeReadMiniSerializer()

    class Meta:
        model = LockerSubscription
        fields = "__all__"


class LockerSubscriptionSlimSerializer(serializers.ModelSerializer):
    student = StudentReadMiniSerializer()
    locker = LockerReadSlimSerializer()
    subscription_request = serializers.StringRelatedField()
    created_by = serializers.StringRelatedField()
    cancelled_by = serializers.StringRelatedField()
    fee = FeeReadMiniSerializer()

    class Meta:
        model = LockerSubscription
        exclude = ["updated_at", "updated_by", "created_at"] # student

class LockerSubscriptionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = LockerSubscription
        fields = [
            "student",
            "locker",
            "academic_year",
            "semester",
            "payment_due_at",
            "created_by",
            "status",
            "payment_status",
        ]
        read_only_fields = ["status", "payment_status", "created_by"]

    def create(self, validated_data):
        # automatically set created_by if request exists
        request = self.context.get("request")
        if request and hasattr(request.user, "id"):
            validated_data["created_by"] = request.user
        return super().create(validated_data)


class LockerSubscriptionCancelSerializer(serializers.ModelSerializer):
    cancellation_reason = serializers.CharField(required=True)

    class Meta:
        model = LockerSubscription
        fields = ["status", "cancelled_at", "cancelled_by", "cancellation_reason"]
        read_only_fields = ["status", "cancelled_at", "cancelled_by"]

    def update(self, instance, validated_data):
        instance.status = LockerSubscription.SubscriptionStatus.CANCELLED
        instance.cancelled_at = timezone.now()
        instance.cancelled_by = self.context["request"].user
        instance.cancellation_reason = validated_data["cancellation_reason"]
        instance.save()
        return instance


class LockerSubscriptionCancelSerializer(serializers.ModelSerializer):
    cancellation_reason = serializers.CharField(required=True)

    class Meta:
        model = LockerSubscription
        fields = ["status", "cancelled_at", "cancelled_by", "cancellation_reason"]
        read_only_fields = ["status", "cancelled_at", "cancelled_by"]

    def update(self, instance, validated_data):
        instance.status = LockerSubscription.SubscriptionStatus.CANCELLED
        instance.cancelled_at = timezone.now()
        instance.cancelled_by = self.context["request"].user
        instance.cancellation_reason = validated_data["cancellation_reason"]
        instance.save()
        return instance


# class LockerFeeRecordSerializer(serializers.ModelSerializer):
#     student_name = serializers.CharField(source="student.full_name", read_only=True)
#     subscription_id = serializers.IntegerField(source="subscription.id", read_only=True)
#     batch_id = serializers.IntegerField(source="batch.id", read_only=True)

#     class Meta:
#         model = LockerFeeRecord
#         fields = [
#             "id",
#             "batch_id",
#             "student",
#             "student_name",
#             "subscription_id",
#             "institute",
#             "semester",
#             "year",
#             "amount",
#             "is_paid",
#             "generated_at",
#         ]
#         read_only_fields = ["generated_at", "batch_id", "student_name"]


# class LockerFeeBatchSerializer(serializers.ModelSerializer):
#     fee_count = serializers.IntegerField(source="fee_records.count", read_only=True)
#     created_by_name = serializers.CharField(
#         source="created_by.username", read_only=True
#     )

#     class Meta:
#         model = LockerFeeBatch
#         fields = [
#             "id",
#             "institute",
#             "semester",
#             "year",
#             "created_by",
#             "created_by_name",
#             "created_at",
#             "fee_count",
#         ]
#         read_only_fields = ["created_at", "fee_count", "created_by_name"]


# class LockerFeeGenerateSerializer(serializers.Serializer):
#     institute_id = serializers.IntegerField()
#     semester = serializers.CharField()
#     year = serializers.IntegerField()
#     force = serializers.BooleanField(default=False)

#     def validate(self, data):
#         institute_id = data["institute_id"]
#         semester = data["semester"]
#         year = data["year"]
#         force = data["force"]

#         # config must exist
#         if not LockerSemesterFeeConfig.objects.filter(
#             institute_id=institute_id, semester=semester, year=year
#         ).exists():
#             raise serializers.ValidationError(
#                 "No locker fee config found for this (institute, semester, year)."
#             )

#         # if not force and batch exists -> block
#         if (
#             not force
#             and LockerFeeBatch.objects.filter(
#                 institute_id=institute_id, semester=semester, year=year
#             ).exists()
#         ):
#             raise serializers.ValidationError(
#                 "Fees already generated for this semester & year. Use force=True to regenerate."
#             )

#         return data
