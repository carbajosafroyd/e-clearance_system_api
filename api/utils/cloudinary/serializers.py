from rest_framework import serializers
from .cloudinary_utils import current_version, upload_images, delete_file, get_file_url


class CloudinaryUploadMixin(serializers.Serializer):
    images = serializers.ListField(
        child=serializers.ImageField(), write_only=True, required=True
    )
    folder = serializers.CharField(write_only=True, required=False)

    image_urls = serializers.ListField(child=serializers.URLField(), read_only=True)

    def handle_cloudinary_upload(self, instance, images, field_name="image_ids", tags=None):
        """
        Upload new images, delete old ones, store public_ids in the model field.

        `instance.folder` and `tags` come from `paths.folder_for` / `paths.tags_for`.
        """
        folder = getattr(instance, "folder", "uploads/")

        # Delete old images - on the account that holds them
        old_version = getattr(instance, "cloudinary_version", None)
        for pid in getattr(instance, field_name, []) or []:
            delete_file(pid, version=old_version)

        # Upload new images - to the current account, and say so on the row
        public_ids = upload_images(images, folder=folder, tags=tags)
        setattr(instance, field_name, public_ids)
        if hasattr(instance, "cloudinary_version"):
            instance.cloudinary_version = current_version()

        # Save instance
        instance.save()
        return instance

    def to_representation(self, instance, field_name="image_ids"):
        ret = super().to_representation(instance)
        pids = getattr(instance, field_name, []) or []
        version = getattr(instance, "cloudinary_version", None)
        ret["image_urls"] = [get_file_url(pid, version=version) for pid in pids]
        return ret


class CloudinaryUploadSerializer(serializers.Serializer):
    """
    Serializer for uploading an image
    """

    image = serializers.ImageField(required=True, help_text="Image file to upload")
    folder = serializers.CharField(
        required=True, help_text="Folder where images where stored"
    )


class CloudinaryResponseSerializer(serializers.Serializer):
    """
    Serializer for returning uploaded image info
    """

    public_id = serializers.CharField(help_text="Cloudinary public ID of the image")
    secure_url = serializers.URLField(help_text="Secure URL of the uploaded image")


class CloudinaryRetrieveSerializer(serializers.Serializer):
    """
    Serializer for retrieving a single image URL
    """

    image_url = serializers.URLField(help_text="URL of the Cloudinary image")


class CloudinaryGetSerializer(serializers.Serializer):
    """
    Serializer for retrieving a single image URL
    """

    public_id = serializers.CharField(
        required=True, help_text="public_id of the Cloudinary image"
    )


class CloudinaryDeleteSerializer(serializers.Serializer):
    """
    Serializer for delete response
    """

    success = serializers.BooleanField(help_text="Indicates if deletion was successful")
    error = serializers.CharField(
        help_text="Error message if deletion failed", required=False
    )
