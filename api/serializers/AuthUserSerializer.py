from rest_framework import serializers
from api.users.models import User
from api.institutes.serializers import InstituteReadSerializer
from api.schools.serializers import SchoolReadSerializer
from api.rbac.permissions import user_permission_codenames
from api.utils.cloudinary.cloudinary_utils import get_file_url
from core import features
from drf_spectacular.utils import extend_schema_field


class AuthUserSerializer(serializers.ModelSerializer):
    """The `/api/v1/me/` payload.

    Beyond the profile fields, this carries the caller's access: `groups`
    (the role names, which the frontend's route guards match against) and
    `permissions` (every ``app_label.codename`` they hold, which is what the
    per-button checks read). `is_superuser` / `is_staff` mirror the two flags
    several viewsets still branch on server-side.

    Both lists come from the same place the API enforces against, so the UI
    can never offer an action the request would then be refused.

    `features` reports which optional modules this deployment runs. The
    frontend has its own build-time flags, but the server's answer is the
    authoritative one - an institute that runs the API with the contribution
    scheme off should see nothing of it whatever the bundle was built with.
    """

    institute = InstituteReadSerializer()
    #: School-wide accounts (School Admins) belong to a school and no
    #: institute; the frontend brands itself from whichever one is set.
    school = SchoolReadSerializer(allow_null=True)
    profile = serializers.SerializerMethodField()
    student = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    features = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "institute",
            "school",
            "username",
            "first_name",
            "last_name",
            "email",
            "profile",
            "student",
            "groups",
            "permissions",
            "features",
            "is_superuser",
            "is_staff",
        ]

    def get_profile(self, obj) -> str:
        return get_file_url(obj.profile, version=obj.cloudinary_version)

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_groups(self, obj) -> list[str]:
        return list(obj.groups.values_list("name", flat=True))

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_permissions(self, obj) -> list[str]:
        return user_permission_codenames(obj)

    @extend_schema_field(serializers.DictField(child=serializers.BooleanField()))
    def get_features(self, obj) -> dict[str, bool]:
        # Per institute: the same build serves institutes running different
        # modules, so "what does this deployment ship" is the wrong question
        # to answer here.
        return features.enabled_features(features.resolve_institute(obj))

    @extend_schema_field(
        serializers.DictField(child=serializers.JSONField(), allow_null=True)
    )
    def get_student(self, obj):
        if hasattr(obj, 'student'):
            from api.students.serializers import StudentReadSlimSerializer
            return StudentReadSlimSerializer(obj.student).data
        return None
