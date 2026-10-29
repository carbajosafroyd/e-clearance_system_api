from rest_framework import serializers
from api.schools.models import School, SchoolBuilding
from api.utils.cloudinary.logo_serializer import (
    BackgroundImageMixin, BackgroundUrlMixin, LogoImageMixin, LogoUrlMixin,
)
from api.utils.email_sender_serializer import (
    EMAIL_SENDER_READ_FIELDS, EMAIL_SENDER_WRITE_FIELDS,
    EmailSenderReadMixin, EmailSenderWriteMixin,
)


class SchoolCreateSerializer(
    LogoImageMixin, BackgroundImageMixin, EmailSenderWriteMixin, serializers.ModelSerializer
):
    """Schema for creating a school. Multipart when an image is attached."""

    class Meta:
        model = School
        fields = [
            "id", "school_name", "short_name", "logo", "logo_image", "remove_logo",
            "background_image", "background_file", "remove_background", "location",
            *EMAIL_SENDER_WRITE_FIELDS,
        ]
        read_only_fields = ["id"]


class SchoolReadSerializer(
    LogoUrlMixin, BackgroundUrlMixin, EmailSenderReadMixin, serializers.ModelSerializer
):
    """Detailed schema for reading/retrieving"""

    class Meta:
        model = School
        fields = [
            "id",
            "school_name",
            "short_name",
            "logo",
            "background_image",
            "location",
            *EMAIL_SENDER_READ_FIELDS,
            "created_at",
            "updated_at",
            # "updated_by",
        ]


class SchoolReadSlimSerializer(LogoUrlMixin, BackgroundUrlMixin, serializers.ModelSerializer):
    class Meta:
        model = School
        fields = ["id", "school_name", "short_name", "logo", "background_image", "location"]


class SchoolUpdateSerializer(
    LogoImageMixin, BackgroundImageMixin, EmailSenderWriteMixin, serializers.ModelSerializer
):
    """
    Schema for updates. `logo_image` / `background_file` replace, `remove_*`
    clears; `email_host_password` replaces, blank keeps, a blank
    `email_host_user` switches the sender off.
    """

    class Meta:
        model = School
        fields = [
            "id", "school_name", "short_name", "logo", "logo_image", "remove_logo",
            "background_image", "background_file", "remove_background", "location",
            *EMAIL_SENDER_WRITE_FIELDS,
        ]
        read_only_fields = ["id"]


class SchoolDeleteSerializer(serializers.ModelSerializer):
    """Schema for confirming deletion"""

    class Meta:
        model = School
        fields = ["id", "school_name", "short_name"]

class SchoolBuildingReadSerializer(serializers.ModelSerializer):
    school = SchoolReadSlimSerializer(read_only=True)
    class Meta:
        model = SchoolBuilding
        fields = "__all__"

class SchoolBuildingReadSlimSerializer(serializers.ModelSerializer):
    school_name = serializers.CharField(source="school.school_name", read_only=True)
    class Meta:
        model = SchoolBuilding
        fields = ["id","school_name","name", "max_floor"]


class SchoolBuildinCreateSerializer(serializers.ModelSerializer):

    class Meta:
        model = SchoolBuilding
        fields = ["name", "max_floor"]


class ShoolBuildingDeleteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolBuilding
        fields = ["id"]
