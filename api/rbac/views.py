"""
Read the role/permission catalog over HTTP.

Two audiences:

* the frontend, which calls ``/api/v1/access/me/`` once after sign-in and
  uses the returned permission list to decide which routes, nav entries and
  buttons to render;
* whoever administers roles, who needs to see what a role actually grants
  without reading `roles.py` on the server.

Assigning roles is deliberately here rather than on the user endpoint: it is
a privilege escalation path, and keeping it in one place with one permission
(`rbac.manage_roles`) makes it easy to audit.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import resolve_actor

from .permissions import (
    ALLOW_ANY,
    DEFAULT,
    ActionPermission,
    user_permission_codenames,
)
from .roles import ROLE_DEFINITIONS, delegable_roles, resolve_permissions
from .scoping import account_in_scope
from .serializers import (
    AssignRoleSerializer,
    FeatureSerializer,
    MyAccessSerializer,
    PermissionSerializer,
    RoleSerializer,
)
from core import features as feature_flags


@extend_schema(tags=["Access Control"])
class AccessViewSet(viewsets.ViewSet):
    """`/api/v1/access/`"""

    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = {
        # Everyone may read their own access - it describes nothing they
        # cannot already discover by clicking around.
        "me": ALLOW_ANY,
        # Which modules the deployment runs is not sensitive - the nav a
        # user can already see says as much - and the frontend needs it
        # before it decides what to render.
        "features": ALLOW_ANY,
        "roles": "rbac.view_role_catalog",
        "permissions": "rbac.view_role_catalog",
        "assign": "rbac.manage_roles",
        DEFAULT: "rbac.view_role_catalog",
    }

    @extend_schema(responses=MyAccessSerializer)
    @action(detail=False, methods=["get"])
    def me(self, request):
        user = request.user
        payload = {
            "username": user.username,
            "is_superuser": user.is_superuser,
            "is_staff": user.is_staff,
            "institute_id": getattr(user, "institute_id", None),
            "roles": list(user.groups.values_list("name", flat=True)),
            "permissions": user_permission_codenames(user),
            # The caller's institute, not the deployment: two institutes on
            # one deployment legitimately run different modules.
            "features": feature_flags.enabled_features(
                feature_flags.resolve_institute(user)
            ),
        }
        return Response(MyAccessSerializer(payload).data)

    @extend_schema(
        responses=FeatureSerializer(many=True),
        description=(
            "Which optional modules your institute runs. A module the build "
            "does not ship has no routes at all, so this is how a client "
            "tells a missing feature apart from a missing permission."
        ),
    )
    @action(detail=False, methods=["get"])
    def features(self, request):
        institute = feature_flags.resolve_institute(request.user)
        return Response(
            FeatureSerializer(
                [
                    {"name": name, "enabled": enabled}
                    for name, enabled in feature_flags.enabled_features(
                        institute
                    ).items()
                ],
                many=True,
            ).data
        )

    @extend_schema(responses=RoleSerializer(many=True))
    @action(detail=False, methods=["get"])
    def roles(self, request):
        # name -> (id, member count). The id is what the invitation
        # endpoint takes (`group_id`), and this catalog was the only place
        # a client could learn which role it was inviting into WITHOUT the
        # id - so the console had to harvest ids from the account list, a
        # workaround that broke the moment that list stopped carrying
        # group columns.
        groups = {
            name: (pk, members)
            for pk, name, members in Group.objects.annotate(
                members=Count("user")
            ).values_list("id", "name", "members")
        }
        payload = [
            {
                "name": name,
                "description": spec.description,
                "inherits": list(spec.inherits),
                "staff_flag": spec.staff_flag,
                "permissions": sorted(resolve_permissions(name)),
                "exists_in_db": name in groups,
                "group_id": groups[name][0] if name in groups else None,
                "member_count": groups[name][1] if name in groups else 0,
            }
            for name, spec in ROLE_DEFINITIONS.items()
        ]
        return Response(RoleSerializer(payload, many=True).data)

    @extend_schema(responses=PermissionSerializer(many=True))
    @action(detail=False, methods=["get"])
    def permissions(self, request):
        """Every permission the database knows about, grouped-friendly."""
        rows = (
            Permission.objects.select_related("content_type")
            .order_by("content_type__app_label", "codename")
            .values_list("content_type__app_label", "codename", "name")
        )
        payload = [
            {
                "app_label": app_label,
                "codename": codename,
                "name": name,
                "label": f"{app_label}.{codename}",
            }
            for app_label, codename, name in rows
        ]
        return Response(PermissionSerializer(payload, many=True).data)

    @extend_schema(
        request=AssignRoleSerializer,
        responses=MyAccessSerializer,
        description=(
            "Replace a user's roles with the given list.\n\n"
            "Bounded two ways for everyone but a superuser. The target must be "
            "in the caller's scope - their own institute, or their whole school "
            "for a school administrator - and is reported as not found "
            "otherwise, the same answer a missing id gets. And only the roles "
            "the caller's own role hands out (`DELEGATION` in "
            "`api/rbac/roles.py`) may be added or removed: any other role the "
            "account holds must be sent back unchanged, or the request is "
            "refused naming it. So an institute administrator can make one of "
            "their people a collection-desk admin, and cannot make anyone a "
            "school administrator - or strip that from one."
        ),
    )
    @action(detail=False, methods=["post"])
    def assign(self, request):
        """Replace a user's roles with the given list."""
        serializer = AssignRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        User = get_user_model()
        try:
            target = User.objects.select_related("institute", "school").get(
                pk=serializer.validated_data["user_id"]
            )
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND
            )

        # Out of scope reads as missing, so this route never confirms that
        # an account outside the caller's institute exists.
        if not account_in_scope(request.user, target):
            return Response(
                {"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND
            )

        if not request.user.is_superuser and target.is_superuser:
            return Response(
                {"detail": "Only a superuser may change a superuser's roles."},
                status=status.HTTP_403_FORBIDDEN,
            )

        wanted = serializer.validated_data["roles"]
        unknown = [name for name in wanted if name not in ROLE_DEFINITIONS]
        if unknown:
            return Response(
                {"detail": f"Unknown role(s): {', '.join(unknown)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        before = sorted(target.groups.values_list("name", flat=True))

        if not request.user.is_superuser:
            # Only the DIFFERENCE is judged: a role the caller may not hand
            # out is fine to leave in place, and the console sends the
            # account's whole set back, locked roles included. What it may
            # not do is add or remove one - which is what the symmetric
            # difference is.
            allowed = delegable_roles(request.user)
            beyond = sorted(set(before).symmetric_difference(wanted) - allowed)
            if beyond:
                offer = ", ".join(sorted(allowed)) or "nothing"
                return Response(
                    {
                        "detail": (
                            f"Your role does not hand out or take away: {', '.join(beyond)}. "
                            f"You may change: {offer}."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
        groups = [Group.objects.get_or_create(name=name)[0] for name in wanted]
        target.groups.set(groups)

        should_be_staff = any(ROLE_DEFINITIONS[name].staff_flag for name in wanted)
        if not target.is_superuser and target.is_staff != should_be_staff:
            target.is_staff = should_be_staff
            target.save(update_fields=["is_staff"])

        actor_type, actor_id = resolve_actor(request.user)
        AuditLogService.log_action(
            system=AuditLog.System.PLATFORM,
            entity_type="UserRoles",
            entity_id=target.pk,
            action=AuditLog.Action.UPDATE,
            actor_type=actor_type,
            actor_id=actor_id,
            old_values={"roles": before},
            new_values={"roles": sorted(wanted)},
        )

        payload = {
            "username": target.username,
            "is_superuser": target.is_superuser,
            "is_staff": target.is_staff,
            "institute_id": getattr(target, "institute_id", None),
            "roles": sorted(wanted),
            "permissions": user_permission_codenames(target),
            "features": feature_flags.enabled_features(
                feature_flags.resolve_institute(target)
            ),
        }
        return Response(MyAccessSerializer(payload).data)
