from rest_framework import serializers
from api.programs.models import Program
from api.institutes import serializers as InstituteSerializers


class ProgramCreateSerializer(serializers.ModelSerializer):
    """Schema for creating a program"""

    class Meta:
        model = Program
        fields = ["name", "status", "institute", "acronym"]


class ProgramReadSerializer(serializers.ModelSerializer):
    """Detailed schema for reading/retrieving"""

    institute = InstituteSerializers.InstituteReadSerializer()

    class Meta:
        model = Program
        fields = "__all__"


class ProgramSlimReadSerializer(serializers.ModelSerializer):
    institute = InstituteSerializers.InstituteReadSlimSerializer(read_only=True)

    class Meta:
        model = Program
        fields = ["id", "name", "status", "institute", "acronym"]

        read_only_fields = fields


class ProgramUpdateSerializer(serializers.ModelSerializer):
    """Schema for updates"""

    class Meta:
        model = Program
        fields = ["name", "status", "institute", "acronym"]


class ProgramDeleteSerializer(serializers.ModelSerializer):
    """Schema for confirming deletion"""

    class Meta:
        model = Program
        fields = ["id", "name"]
