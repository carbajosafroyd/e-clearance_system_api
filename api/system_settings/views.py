from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    extend_schema,
    extend_schema_view,
    OpenApiExample,
    OpenApiParameter,
)
from api.institutes.models import Institute
from api.rbac import ActionPermission, crud
from api.rbac.scoping import school_id_of
from api.utils.custom_paginations import CustomPagination
from .filters import SystemSettingFilterSet
from core import features as feature_flags
from core import setting_buckets
from core.choices import SystemScope
from .models import SystemSetting
from .serializers import (
    BulkUpdateSettingsSerializer,
    FeatureToggleSerializer,
    SettingBucketSerializer,
    SystemSettingSerializer,
)


@extend_schema(tags=["System Settings"])
@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name="system",
                description=(
                    "Return only the settings for one system, plus the "
                    "platform-wide rows every system needs (the academic "
                    "year and semester). Omit for everything this account "
                    "can reach."
                ),
                required=False,
                enum=SystemScope.values,
            )
        ]
    )
)
class SystemSettingViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing System Settings.
    Strictly isolated to the user's connected institute.
    """

    serializer_class = SystemSettingSerializer
    permission_classes = [IsAuthenticated, ActionPermission]

    # Pages are opt-in - see `paginate_queryset`.
    pagination_class = CustomPagination
    filterset_class = SystemSettingFilterSet
    search_fields = ["setting_key", "description"]
    ordering_fields = [
        "setting_key",
        "system",
        "updated_at",
        "institute__institute_name",
        "institute__school__school_name",
    ]
    required_permissions = crud(
        "system_settings",
        "systemsetting",
        # The coarse permission is the door, not the decision. Which keys a
        # caller may actually write is `core.setting_buckets`, checked per
        # key below - holding this alone changes nothing on its own.
        bulk_update="system_settings.bulk_change_systemsetting",
        # Reading which modules are on is not an administrative act - the
        # same answer is already on `/api/v1/me/`. This route exists so a
        # settings page can also see what is *available* and why.
        features="system_settings.view_systemsetting",
        # Asking what you may edit is not itself an administrative act, and
        # the answer is about the caller rather than about the institute.
        buckets="system_settings.view_systemsetting",
    )
    queryset = SystemSetting.objects.none()

    def reachable_institutes(self):
        """Whose settings this caller may see and write.

        Everybody is scoped to their own institute. A school administrator
        answers for the institutes under them, so `manage_school_settings`
        widens the reach to their school - and stops there. Reading it off a
        permission rather than a group name means a deployment can move the
        authority without editing this file, and a school administrator with
        no school set reaches nothing rather than everything.
        """
        user = self.request.user

        if getattr(user, "is_superuser", False):
            return Institute.objects.all()

        # Through `user.school` or through the institute: a school
        # administrator attached only via their institute used to fall to
        # the one-institute branch and see a sixth of their school.
        school_id = school_id_of(user)
        if user.has_perm(setting_buckets.SCHOOL_WIDE) and school_id:
            return Institute.objects.filter(school_id=school_id)

        # `institute` is nullable, and `pk=None` matches nothing - which is
        # the right answer for an account that belongs to no institute.
        return Institute.objects.filter(pk=user.institute_id)

    def get_queryset(self):
        """
        Tenant isolation, plus the optional `?system=` narrowing.
        """
        # Schema generation runs this without a logged-in user, and
        # AnonymousUser has no institute.
        if getattr(self, "swagger_fake_view", False):
            return SystemSetting.objects.none()

        # institute__school: the serializer names both for the console's
        # list, which groups rows by institute. Ordered school, institute,
        # key so a page of a superuser's rows reads as one institute's
        # settings after another rather than an interleaving.
        queryset = (
            SystemSetting.objects.filter(institute__in=self.reachable_institutes())
            .select_related("institute__school")
            .order_by(
                "institute__school__school_name",
                "institute__institute_name",
                "setting_key",
            )
        )

        # `?system=` (one system's rows plus the platform-wide ones) and the
        # rest of the narrowing live in `SystemSettingFilterSet`; an unknown
        # system is a 400 from the filter's own validation.
        return queryset

    def paginate_queryset(self, queryset):
        """
        Pages only when asked for.

        The collection system and the student portal read an institute's
        settings whole - a dozen rows - as a bare array, and their stores
        assign it straight to state. Wrapping it in a page envelope would
        break every one of them for the sake of the console, which is the
        only client that reads across institutes. So a page is served only
        when the request carries `current_page` or `per_page`; without
        either the response is the array it always was.
        """
        params = self.request.query_params
        if "current_page" not in params and "per_page" not in params:
            return None
        return super().paginate_queryset(queryset)

    def target_institute(self):
        """Which institute a write lands on.

        Own institute unless `institute` says otherwise, and then only one
        this caller can already reach - so the widened read scope and the
        write scope cannot drift apart.
        """
        request = self.request
        requested = request.data.get("institute") or request.query_params.get(
            "institute"
        )

        if requested in (None, ""):
            institute = request.user.institute
            if institute is None:
                raise ValidationError(
                    {
                        "institute": (
                            "Your account belongs to no institute; name the "
                            "one to write to."
                        )
                    }
                )
            return institute

        institute = self.reachable_institutes().filter(pk=requested).first()
        if institute is None:
            raise PermissionDenied(
                "You are not allowed to change settings for that institute."
            )
        return institute

    def _assert_may_change(self, setting_key: str) -> None:
        """Refuse a key outside the caller's buckets.

        The single-row routes need this as much as `bulk_update` does. Without
        it `change_systemsetting` is a way straight past the split: PATCH the
        maintenance row by id and the bucket never gets consulted.
        """
        if not setting_buckets.may_change(self.request.user, setting_key):
            raise PermissionDenied(
                f"You are not allowed to change: {setting_key}"
            )

    def perform_create(self, serializer):
        """
        Assign the setting to the target institute, and stamp its system.
        """
        key = serializer.validated_data.get("setting_key", "")
        self._assert_may_change(key)
        serializer.save(
            institute=self.target_institute(),
            # Derived, never taken from the request. `system` decides which
            # frontend is handed the row, so letting a caller set it would let
            # them hide a setting from the app that enforces it.
            system=setting_buckets.system_for(key),
        )

    def perform_update(self, serializer):
        # Both keys, because `setting_key` is writable: renaming a row you own
        # into one you do not would otherwise be the way in.
        current = serializer.instance.setting_key
        self._assert_may_change(current)
        incoming = serializer.validated_data.get("setting_key", current)
        self._assert_may_change(incoming)
        serializer.save(system=setting_buckets.system_for(incoming))

    def perform_destroy(self, instance):
        # Deleting the maintenance row is a way of lifting maintenance.
        self._assert_may_change(instance.setting_key)
        instance.delete()

    @extend_schema(
        request=BulkUpdateSettingsSerializer,
        responses={200: SystemSettingSerializer(many=True)},
        examples=[
            OpenApiExample(
                "Valid Bulk Update",
                value={
                    "settings": {
                        "current_school_year": "2026-2027",
                        "active_semester": "2nd",
                    }
                },
                request_only=True,
            )
        ],
        description="Update multiple system settings at once. If a key doesn't exist, it will be created.",
    )
    @action(detail=False, methods=["patch"], url_path="bulk-update")
    def bulk_update(self, request):
        """
        Custom endpoint to handle bulk updating/creating settings from a frontend form.
        """
        settings_data = request.data.get("settings", {})

        if not isinstance(settings_data, dict):
            return Response(
                {"detail": "Payload must contain a 'settings' dictionary."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_institute = self.target_institute()
        updated_settings = []

        # Which keys this caller may write at all. Checked before the feature
        # rules and before anything is saved, so a payload mixing a key they
        # own with one they do not writes neither - a half-applied settings
        # form is worse than a refused one.
        forbidden = setting_buckets.refused_keys(request.user, settings_data.keys())
        if forbidden:
            return Response(
                {
                    "detail": (
                        "You are not allowed to change: " + ", ".join(forbidden)
                    ),
                    "code": "setting_not_permitted",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # Feature rows get their own handling: they are booleans with a
        # dependency between them, and an institute may not be entitled to
        # every one of them.
        rejected = self._reject_unavailable(settings_data, user_institute)
        if rejected:
            return Response(
                {
                    "detail": (
                        "Your institute cannot switch these on: "
                        + ", ".join(sorted(rejected))
                    ),
                    "code": "feature_not_available",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        settings_data = self._with_feature_cascade(settings_data)

        for key, value in settings_data.items():
            setting_obj, created = SystemSetting.objects.update_or_create(
                institute=user_institute,
                setting_key=key,
                defaults={
                    "setting_value": str(value),
                    "system": setting_buckets.system_for(key),
                },
            )
            updated_settings.append(setting_obj)

        response_serializer = self.get_serializer(updated_settings, many=True)
        return Response(response_serializer.data, status=status.HTTP_200_OK)

    @staticmethod
    def _reject_unavailable(payload: dict, institute) -> list[str]:
        """Feature keys this institute is not entitled to turn on.

        Refused rather than ignored. Silently dropping the write would leave
        a settings page showing a switch that flips back every time it is
        saved, and nobody would know why.
        """
        refused = []
        for feature in feature_flags.ALL_FEATURES:
            key = feature_flags.setting_key(feature)
            if key not in payload:
                continue
            wants_on = feature_flags.parse_flag(payload[key])
            if wants_on and not feature_flags.is_available(feature, institute):
                refused.append(feature)
        return refused

    @staticmethod
    def _with_feature_cascade(payload: dict) -> dict:
        """Force the parent/child rule before anything is written.

        Switching Contributions off has to take the points scheme with it. A
        stored pair saying otherwise would disagree with every check that
        reads them, which is an afternoon of somebody's life.
        """
        prefix = feature_flags.SETTING_PREFIX
        wanted = {
            key[len(prefix):]: feature_flags.parse_flag(value)
            for key, value in payload.items()
            if key.startswith(prefix)
        }
        if not wanted:
            return payload

        resolved = feature_flags.apply_cascade(wanted)
        out = dict(payload)
        for feature, value in resolved.items():
            out[f"{prefix}{feature}"] = str(value).lower()
        return out

    @extend_schema(
        responses={200: SettingBucketSerializer(many=True)},
        description=(
            "Which sections of the settings this caller may edit. Needed "
            "because `can_change` on a row cannot answer for a section whose "
            "row does not exist yet - a fresh institute that has never been "
            "closed has no maintenance row, and the page still has to decide "
            "whether to offer the control."
        ),
    )
    @action(detail=False, methods=["get"])
    def buckets(self, request):
        changeable = set(setting_buckets.changeable_buckets(request.user))
        return Response(
            SettingBucketSerializer(
                [
                    {
                        "bucket": bucket,
                        "can_change": bucket in changeable,
                    }
                    for bucket in setting_buckets.ALL_BUCKETS
                ],
                many=True,
            ).data
        )

    @extend_schema(
        responses=FeatureToggleSerializer(many=True),
        description=(
            "The optional modules, as a settings page needs them: whether "
            "each is on, whether this institute may run it at all, and what "
            "it depends on. `available: false` means the switch should be "
            "shown disabled rather than hidden - an absent row reads as a "
            "bug, a disabled one reads as a decision."
        ),
    )
    @action(detail=False, methods=["get"])
    def features(self, request):
        return Response(
            FeatureToggleSerializer(
                feature_flags.feature_settings(request.user.institute), many=True
            ).data
        )
