# from rest_framework import serializers, validators
# from api.systems.models import System


# class SystemSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = System
#         fields = "__all__"

#         read_only_fields = ["id", "created_at", "updated_at"]

#         validators = [
#             validators.UniqueTogetherValidator(
#                 queryset=System.objects.all(),
#                 fields=["name"],
#                 message="Name of the System already EXIST.",
#             )
#         ]


# class SystemReadSlimSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = System
#         fields = ["id","name"]

#         read_only_fields = ["name"]
