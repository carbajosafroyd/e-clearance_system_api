"""
Images that are uploaded, not pasted.

`School.logo` and `Institute.logo` are `TextField`s that originally took
whatever string the client sent - in practice an absolute URL to an image
hosted somewhere else, which broke the day that somewhere else moved. The
mixins below turn such a field into a proper upload while keeping the column:
the row stores a Cloudinary public id, the read side serialises it as a URL,
and rows that still hold a plain URL from before keep working
(`resolve_image_url` passes those through).

Each image a model carries is a *slot*: the column, the write field that
uploads into it, the flag that clears it, and the kind of folder it is filed
under. A serializer stacks one mixin per slot and the shared base does the
work once for all of them:

    class SchoolUpdateSerializer(LogoImageMixin, BackgroundImageMixin, ModelSerializer):
        class Meta:
            model = School
            fields = [..., "logo", "logo_image", "remove_logo",
                      "background_image", "background_file", "remove_background"]

Where the file lands is not the serializer's to say: `paths.folder_for(row,
slot.kind)` files it under the row's own folder (`schools/1/logos`,
`schools/1/institutes/2/backgrounds`), tagged with its owner. See paths.py
for the tree.

Write with multipart:

    logo_image / background_file      an image file - sets or replaces it
    remove_logo / remove_background   true - clears it (and deletes the file)

Sending both for one slot is refused rather than guessed at. Replacing
uploads BEFORE it deletes, so a rejected file leaves the old image in place;
see `cloudinary_utils.replace_image` for the reasoning.
"""
from dataclasses import dataclass

from rest_framework import serializers

from .cloudinary_utils import (
    current_version, remove_image, replace_image, resolve_image_url, upload_image,
)
from .paths import Kind, folder_for, tags_for


@dataclass(frozen=True)
class ImageSlot:
    #: the model column holding the public id (or a legacy URL)
    field: str
    #: the write-only upload field
    upload: str
    #: the write-only "clear it" flag
    remove: str
    #: which leaf folder under the row's own tree the file goes in
    kind: Kind
    #: the field's label in error messages
    label: str
    #: the column holding the Cloudinary account version the image lives on
    version_field: str


class CloudinaryImageSlotsMixin(serializers.Serializer):
    """
    Shared create/update for every image slot a serializer declares.

    Subclasses (the per-slot mixins) contribute their slot through
    `IMAGE_SLOTS`; the union across the MRO is what gets processed, so a
    serializer with two slots runs this once, not once per mixin.
    """

    IMAGE_SLOTS: tuple[ImageSlot, ...] = ()

    @classmethod
    def _slots(cls) -> list[ImageSlot]:
        seen: dict[str, ImageSlot] = {}
        for klass in cls.__mro__:
            for slot in getattr(klass, "IMAGE_SLOTS", ()):
                seen.setdefault(slot.field, slot)
        return list(seen.values())

    def validate(self, attrs):
        attrs = super().validate(attrs)
        for slot in self._slots():
            if attrs.get(slot.remove) and attrs.get(slot.upload):
                raise serializers.ValidationError(
                    {slot.upload: f"Choose one: upload a new {slot.label}, or remove the current one."}
                )
        return attrs

    def _pop_slots(self, validated_data):
        """Take every slot's write fields out of the data the model must not see."""
        return {
            slot.field: (
                validated_data.pop(slot.upload, None),
                validated_data.pop(slot.remove, False),
            )
            for slot in self._slots()
        }

    def create(self, validated_data):
        pending = self._pop_slots(validated_data)
        # The row first: its id is part of the folder the image goes in.
        instance = super().create(validated_data)

        changed = []
        for slot in self._slots():
            image, _remove = pending[slot.field]
            if not image:
                continue
            try:
                setattr(
                    instance,
                    slot.field,
                    upload_image(
                        image, folder=folder_for(instance, slot.kind), tags=tags_for(instance)
                    ),
                )
            except ValueError as exc:
                # The row exists by now; a bad file should not leave a
                # half-made record behind.
                instance.delete()
                raise serializers.ValidationError({slot.upload: str(exc)})
            # Uploads go to the current account; the row remembers which.
            setattr(instance, slot.version_field, current_version())
            changed += [slot.field, slot.version_field]
        if changed:
            instance.save(update_fields=changed)
        return instance

    def update(self, instance, validated_data):
        pending = self._pop_slots(validated_data)
        instance = super().update(instance, validated_data)

        changed = []
        for slot in self._slots():
            image, remove = pending[slot.field]
            if remove:
                remove_image(getattr(instance, slot.field), version=getattr(instance, slot.version_field))
                setattr(instance, slot.field, None)
                setattr(instance, slot.version_field, None)
                changed += [slot.field, slot.version_field]
            elif image:
                try:
                    setattr(
                        instance,
                        slot.field,
                        replace_image(
                            getattr(instance, slot.field),
                            image,
                            folder=folder_for(instance, slot.kind),
                            old_version=getattr(instance, slot.version_field),
                            tags=tags_for(instance),
                        ),
                    )
                except ValueError as exc:
                    raise serializers.ValidationError({slot.upload: str(exc)})
                setattr(instance, slot.version_field, current_version())
                changed += [slot.field, slot.version_field]
        if changed:
            instance.save(update_fields=changed)
        return instance


# ---------------------------------------------------------------- logo


class LogoUrlMixin(serializers.Serializer):
    """Read side only: `logo` comes out as a URL whatever the column holds."""

    logo = serializers.SerializerMethodField()

    def get_logo(self, obj) -> str | None:
        return resolve_image_url(obj.logo, version=getattr(obj, "logo_cloudinary_version", None))


class LogoImageMixin(LogoUrlMixin, CloudinaryImageSlotsMixin):
    """Read side plus the two write fields."""

    IMAGE_SLOTS = (
        ImageSlot("logo", "logo_image", "remove_logo", Kind.LOGOS, "logo", "logo_cloudinary_version"),
    )

    logo_image = serializers.ImageField(required=False, allow_null=True, write_only=True)
    remove_logo = serializers.BooleanField(required=False, default=False, write_only=True)


# ---------------------------------------------------------------- background image


class BackgroundUrlMixin(serializers.Serializer):
    """Read side only: `background_image` comes out as a URL whatever the column holds."""

    background_image = serializers.SerializerMethodField()

    def get_background_image(self, obj) -> str | None:
        return resolve_image_url(
            obj.background_image, version=getattr(obj, "background_cloudinary_version", None)
        )


class BackgroundImageMixin(BackgroundUrlMixin, CloudinaryImageSlotsMixin):
    """
    The wide picture behind a sign-in page or a header - the frontend falls
    back to its own default where a row has none.
    """

    IMAGE_SLOTS = (
        ImageSlot(
            "background_image",
            "background_file",
            "remove_background",
            Kind.BACKGROUNDS,
            "background image",
            "background_cloudinary_version",
        ),
    )

    background_file = serializers.ImageField(required=False, allow_null=True, write_only=True)
    remove_background = serializers.BooleanField(required=False, default=False, write_only=True)
