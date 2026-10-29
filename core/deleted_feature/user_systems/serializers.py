# from rest_framework import serializers, validators

# from api.user_systems.models import UserSystem


# class UserSystemReadSerializer(serializers.ModelSerializer):
#     user_id = serializers.CharField(source="user.id", read_only=True)
#     user_name = serializers.CharField(source="user.username", read_only=True)
#     system_id = serializers.CharField(source="system.id", read_only=True)
#     system_name = serializers.CharField(source="system.name", read_only=True)

#     class Meta:
#         model = UserSystem
#         fields = ["user_id", "user_name", "system_id", "system_name"]


# class UserSystemSerializer(serializers.ModelSerializer):

#     class Meta:
#         model = UserSystem
#         fields = ["user", "system"]
#         read_only_fields = ["id", "created_at", "updated_at"]


# class UserSystemCreateSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = UserSystem
#         fields = ["user", "system"]

#         validators = [
#             validators.UniqueTogetherValidator(
#                 queryset=UserSystem.objects.all(),
#                 fields=["user", "system"],
#                 message="This user is already linked to this system.",
#             )
#         ]


# class UserSysemUpdateSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = UserSystem
#         fields = ["user", "system"]
        
#         validators = [
#             validators.UniqueTogetherValidator(
#                 queryset=UserSystem.objects.all(),
#                 fields=["user", "system"],
#                 message="This user is already linked to this system.",
#             )
#         ]
