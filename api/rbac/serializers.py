from rest_framework import serializers


class RoleSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField()
    inherits = serializers.ListField(child=serializers.CharField())
    staff_flag = serializers.BooleanField()
    permissions = serializers.ListField(child=serializers.CharField())
    exists_in_db = serializers.BooleanField()
    #: The auth_group primary key - what `POST /admin/invitations/` takes as
    #: `group_id`. Null until `seed_rbac` has created the group.
    group_id = serializers.IntegerField(allow_null=True)
    member_count = serializers.IntegerField()


class PermissionSerializer(serializers.Serializer):
    codename = serializers.CharField()
    app_label = serializers.CharField()
    name = serializers.CharField()
    label = serializers.CharField(help_text='"app_label.codename"')


class FeatureSerializer(serializers.Serializer):
    """Which optional modules this deployment runs."""

    name = serializers.CharField()
    enabled = serializers.BooleanField()


class MyAccessSerializer(serializers.Serializer):
    """What the signed-in caller may do - the frontend's RBAC source."""

    username = serializers.CharField()
    is_superuser = serializers.BooleanField()
    is_staff = serializers.BooleanField()
    institute_id = serializers.IntegerField(allow_null=True)
    roles = serializers.ListField(child=serializers.CharField())
    permissions = serializers.ListField(child=serializers.CharField())
    #: Same map `/api/v1/me/` returns; a permission belonging to a disabled
    #: feature is already filtered out of `permissions`.
    features = serializers.DictField(child=serializers.BooleanField())


class AssignRoleSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    roles = serializers.ListField(child=serializers.CharField(), allow_empty=True)
