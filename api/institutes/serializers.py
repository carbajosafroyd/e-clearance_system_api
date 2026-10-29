from rest_framework import serializers
from api.institutes.models import Institute
from api.schools.serializers import SchoolReadSerializer
from api.utils.cloudinary.logo_serializer import (
    BackgroundImageMixin, BackgroundUrlMixin, LogoImageMixin, LogoUrlMixin,
)
from api.utils.email_sender_serializer import (
    EMAIL_SENDER_READ_FIELDS, EMAIL_SENDER_WRITE_FIELDS,
    EmailSenderReadMixin, EmailSenderWriteMixin,
)


class InstituteCreateSerializer(
    LogoImageMixin, BackgroundImageMixin, EmailSenderWriteMixin, serializers.ModelSerializer
):
    """Schema for creating an institute. Multipart when an image is attached."""

    class Meta:
        model = Institute
        fields = [
            "id", "institute_name", "acronym", "logo", "logo_image", "remove_logo",
            "background_image", "background_file", "remove_background", "school",
            *EMAIL_SENDER_WRITE_FIELDS,
        ]
        read_only_fields = ["id"]


class InstituteReadSerializer(
    LogoUrlMixin, BackgroundUrlMixin, EmailSenderReadMixin, serializers.ModelSerializer
):
    """Detailed schema for reading/retrieving"""

    school = SchoolReadSerializer()

    class Meta:
        model = Institute
        # Listed, not `__all__`: the row also carries the encrypted app
        # password, and that column must never be serialised.
        fields = [
            "id",
            "institute_name",
            "acronym",
            "logo",
            "background_image",
            "logo_cloudinary_version",
            "background_cloudinary_version",
            "school",
            *EMAIL_SENDER_READ_FIELDS,
            "created_at",
            "updated_at",
        ]


class InstituteReadSlimSerializer(
    LogoUrlMixin, BackgroundUrlMixin, EmailSenderReadMixin, serializers.ModelSerializer
):
    school_id = serializers.IntegerField(source="school.id", read_only=True)
    school_name = serializers.CharField(source="school.school_name", read_only=True)

    class Meta:
        model = Institute
        # The sender fields ride along on the list row so the console's edit
        # form knows whether a password is on file without a second request.
        fields = [
            "id", "institute_name", "acronym", "school_id", "school_name", "logo",
            "background_image", *EMAIL_SENDER_READ_FIELDS,
        ]


class InstituteUpdateSerializer(
    LogoImageMixin, BackgroundImageMixin, EmailSenderWriteMixin, serializers.ModelSerializer
):
    """
    Schema for updates. `logo_image` / `background_file` replace, `remove_*`
    clears; `email_host_password` replaces, blank keeps, a blank
    `email_host_user` switches the sender off.
    """

    class Meta:
        model = Institute
        fields = [
            "id", "institute_name", "acronym", "logo", "logo_image", "remove_logo",
            "background_image", "background_file", "remove_background", "school",
            *EMAIL_SENDER_WRITE_FIELDS,
        ]
        read_only_fields = ["id"]


class InstituteDeleteSerializer(serializers.ModelSerializer):
    """Schema for confirming deletion"""

    class Meta:
        model = Institute
        fields = ["id", "institute_name"]
