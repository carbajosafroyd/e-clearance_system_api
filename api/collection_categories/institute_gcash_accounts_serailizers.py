from rest_framework import serializers
from .models import InstituteGcashAccount
from api.utils.cloudinary.cloudinary_utils import (
    current_version,
    upload_image,
    get_file_url,
    replace_image,
)
from api.institutes.serializers import InstituteReadSlimSerializer
from api.utils.cloudinary.paths import Kind, folder_for, tags_for


class CreateIntituteGcashAccountSerializer(serializers.ModelSerializer):
    gcash_qr_image = serializers.ImageField(required=True, write_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = InstituteGcashAccount
        fields = ["gcash_qr_image", "image_url", "number", "hint"]

    def create(self, validated_data):
        gcash_qr_image = validated_data.pop("gcash_qr_image")
        institute = self.context["request"].user.institute
        validated_data["institute"] = institute
        # Filed under the institute whose account it is - see paths.py.
        validated_data["gcash_qr"] = upload_image(
            gcash_qr_image,
            folder=folder_for(institute, Kind.GCASH_ACCOUNTS),
            tags=tags_for(institute),
        )
        validated_data["cloudinary_version"] = current_version()
        return super().create(validated_data)

    def get_image_url(self, obj) -> str:
        return get_file_url(obj.gcash_qr, version=obj.cloudinary_version)


class ReadInsituteGcashSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()
    institute = InstituteReadSlimSerializer()
    times_used = serializers.SerializerMethodField()

    class Meta:
        model = InstituteGcashAccount
        # `id` is what a client keys the account picker on; `status` is why
        # an account is or is not being offered; `times_used` is how many
        # approved submissions the *requesting student* has sent to it.
        fields = ["id", "image_url", "number", "hint", "status", "times_used", "institute"]

    def get_image_url(self, obj) -> str:
        return get_file_url(obj.gcash_qr, version=obj.cloudinary_version)

    def get_times_used(self, obj) -> int:
        # Supplied by the viewset for a student's request; the console's
        # own list has no student to count for and gets zeros.
        return self.context.get("times_used", {}).get(obj.number, 0)


class UpdateInsituteGcashSerializer(serializers.ModelSerializer):
    gcash_qr_image = serializers.ImageField(required=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = InstituteGcashAccount
        fields = ["gcash_qr_image", "image_url", "number", "hint"]

    def update(self, instance, validated_data):
        new_image = validated_data.pop("gcash_qr_image", None)

        instance = super().update(instance, validated_data)

        if new_image:
            # Upload first, delete second - a rejected file must leave the
            # QR students are scanning right now untouched.
            try:
                instance.gcash_qr = replace_image(
                    instance.gcash_qr,
                    new_image,
                    folder=folder_for(instance.institute, Kind.GCASH_ACCOUNTS),
                    old_version=instance.cloudinary_version,
                    tags=tags_for(instance.institute),
                )
                instance.cloudinary_version = current_version()
                instance.save(update_fields=["gcash_qr", "cloudinary_version"])
            except ValueError as e:
                raise serializers.ValidationError({"gcash_qr_image": str(e)})

        return instance

    def get_image_url(self, obj) -> str:
        return get_file_url(obj.gcash_qr, version=obj.cloudinary_version)


class DeleteInsituteGcashSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstituteGcashAccount
        fields = ["id"]