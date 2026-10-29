from rest_framework import serializers

from core import setting_buckets

from .models import SystemSetting


class SystemSettingSerializer(serializers.ModelSerializer):
    """One setting row, plus whether the caller is allowed to change it.

    Reading is open to every signed-in account and stays that way - the whole
    app filters by the current term and renders a module only if the row says
    so, and a student who could not read these could not use the app.

    Writing is not, so each row carries `can_change`. A settings page can then
    render a control it may not use as disabled with a reason, rather than
    re-deriving the bucket rule on the client and disagreeing with the server
    the day one of them changes.
    """

    can_change = serializers.SerializerMethodField()
    #: Which permission governs this key, for a page that groups by section.
    bucket = serializers.SerializerMethodField()
    #: Named, not just numbered: the console lists rows across institutes
    #: and a page of them needs a heading per institute without a lookup.
    institute_name = serializers.CharField(source="institute.institute_name", read_only=True)
    school_id = serializers.IntegerField(source="institute.school_id", read_only=True)
    school_name = serializers.CharField(source="institute.school.school_name", read_only=True)

    class Meta:
        model = SystemSetting
        fields = [
            "id",
            "institute",
            "institute_name",
            "school_id",
            "school_name",
            "setting_key",
            "setting_value",
            "description",
            "system",
            "updated_at",
            "can_change",
            "bucket",
        ]
        # `system` is derived from the key by `setting_buckets.system_for` and
        # stamped on write. It decides which frontend is handed the row, so a
        # caller who could set it could hide a setting from the app that
        # enforces it.
        read_only_fields = [
            "id",
            "institute",
            "institute_name",
            "school_id",
            "school_name",
            "system",
            "updated_at",
            "can_change",
            "bucket",
        ]

    def get_can_change(self, obj) -> bool:
        request = self.context.get("request")
        return setting_buckets.may_change(
            getattr(request, "user", None), obj.setting_key
        )

    def get_bucket(self, obj) -> str | None:
        return setting_buckets.bucket_of(obj.setting_key)


class BulkUpdateSettingsSerializer(serializers.Serializer):
    """
    A simple dictionary serializer for the Swagger documentation.
    Expects a payload like: {"current_school_year": "2026-2027", "maintenance": "true"}
    """

    settings = serializers.DictField(
        child=serializers.CharField(),
        help_text="A dictionary of key-value pairs to update or create.",
    )


class FeatureToggleSerializer(serializers.Serializer):
    """One optional module, as a settings page needs to render it."""

    feature = serializers.CharField()
    #: The `SystemSetting` key to write, so the UI does not rebuild the name.
    key = serializers.CharField()
    #: May this institute run it at all? A restricted module reads as
    #: unavailable rather than merely off, or somebody keeps trying.
    available = serializers.BooleanField()
    enabled = serializers.BooleanField()
    #: False when the institute has no row yet and is inheriting the default.
    configured = serializers.BooleanField()
    #: The feature this one cannot outlive, if any.
    requires = serializers.CharField(allow_null=True)


class SettingBucketSerializer(serializers.Serializer):
    """One section of the settings page, and whether the caller may edit it.

    Separate from `can_change` on a row because a section can exist with no
    row behind it: an institute that has never been closed has no maintenance
    row, and the page still has to decide whether to offer the control.
    """

    bucket = serializers.CharField()
    can_change = serializers.BooleanField()
