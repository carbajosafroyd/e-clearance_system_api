from datetime import datetime
import threading
from rest_framework import viewsets, status
from rest_framework.permissions import (
    IsAuthenticated,
    AllowAny,
    IsAdminUser,
    DjangoModelPermissions,
)
from drf_spectacular.utils import extend_schema, OpenApiResponse

from django.db.models import Count
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from api.audits.models import AuditLog
from api.audits.services import AuditLogService
from api.audits.utils import resolve_actor
from api.rbac.roles import Roles
from api.rbac.scoping import (
    is_school_wide,
    is_student_only,
    school_id_of,
    scope_queryset,
)
from api.change_email.services import (
    EmailChangeError,
    cancel_open_requests,
    start_admin_request,
)
from api.change_email.utils import CONFIRM_EXPIRY_MINUTES
from api.change_password.services import mask_email, start_password_reset
from api.change_password.utils import OTP_EXPIRY_MINUTES
from api.utils.cloudinary.cloudinary_utils import remove_image
from api.users.filters import UserFilterSet
from api.users.models import User
from api.users.student_invites import (
    create_student_invitations,
    parse_invite_file,
    render_template_workbook,
    send_invitations_in_background,
    validate_rows,
)
from api.users.serializers import (
    AdminEmailChangeSerializer,
    AdminSetPasswordSerializer,
    SetScopeSerializer,
    UserCreateSerializer,
    UserReadSerializer,
    UserUpdateSerializer,
    UserDeleteSerializer,
    ChangePasswordSerializer,
    UserReadSlimSerializer,
)
from rest_framework.decorators import action
from rest_framework.response import Response
from api.rbac import ALLOW_ANY, ActionPermission, crud
from api.utils.custom_paginations import CustomPagination

from drf_spectacular.utils import extend_schema

from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes

from rest_framework.views import APIView
from django.conf import settings
from core.services.email_branding import email_brand
from core.services.links import backend_link
from core.services.email_sender import email_sender
from core.services.send_email_v2 import send_bulk_emails_async, send_email_async
from .serializers import (
    BulkUserInviteSerializer,
    RegisterSerializer,
    UserInviteSerializer,
)

from django.core.mail import EmailMultiAlternatives
from django.utils.html import format_html

from django.shortcuts import render
from django.http import HttpResponse
from django.db import transaction
from rest_framework.parsers import FormParser, MultiPartParser

from django.core import mail
from django.template.loader import render_to_string


def _invite_context(registration_token, inviter) -> dict:
    """
    What an invitation email needs to be useful on its own: who it is from,
    what the account will be called, and the one-time password to sign in
    with. Branding follows the *inviter's* school - the recipient has no
    account yet, so theirs is the only one there is.
    """
    return {
        # Branded with where the ACCOUNT will live, not where the inviter
        # sits: a school administrator invites into any institute of their
        # school, and the email should name that institute.
        **email_brand(
            inviter,
            school=registration_token.school,
            institute=registration_token.institute,
        ),
        "link": backend_link("/api/v1/register/", token=registration_token.token),
        "group_name": registration_token.group.name,
        "email": registration_token.email,
        "username": registration_token.username,
        "temporary_password": getattr(registration_token, "raw_password", ""),
        "expiry_days": getattr(settings, "INVITE_EXPIRY_DAYS", 3),
        "invited_by": (inviter.get_full_name() or inviter.username)
        if inviter is not None
        else None,
    }


@extend_schema(tags=["User"])
class UserViewSet(viewsets.ModelViewSet):
    # institute__school + school: the read serializers nest InstituteReadSlim
    # (which reads school.id/school_name) and SchoolReadSlim on every row.
    queryset = (
        User.objects.select_related("institute__school", "school")
        .order_by("id")
    )
    pagination_class = CustomPagination  # list actions use this
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = crud(
        "users",
        "user",
        # Anyone may change their own password; the serializer verifies the
        # current one, and `get_queryset` never enters into it.
        change_password=ALLOW_ANY,
        # Setting somebody else's is its own permission rather than
        # `change_user`: students hold `change_user` for their own profile
        # (see STUDENT_SELF_SERVICE in rbac/roles.py), and this route skips
        # the current-password check, so bundling the two would let a
        # student reset their own password from an unattended session.
        set_password="users.set_user_password",
        # Emailing a reset code is the gentler form of the same authority:
        # the administrator never learns the new password.
        send_password_reset="users.set_user_password",
        # Pointing the account at a new email address (POST) or cancelling
        # a pending one (DELETE). Its own permission: it decides where the
        # password-reset codes go from now on, which is the account.
        change_email="users.change_user_email",
        # Moving an account to a school or an institute. The same authority
        # as changing its roles - placement is half of what a role means -
        # and bounded the same way inside the action.
        set_scope="rbac.manage_roles",
    )

    filterset_class = UserFilterSet
    search_fields = ["username", "first_name", "last_name", "email"]
    ordering_fields = [
        "id",
        "date_joined",
        "last_login",
        "username",
        "first_name",
        "last_name",
        "email",
        "is_active",
        "institute__institute_name",
    ]

    def get_queryset(self):
        user = self.request.user
        queryset = self.queryset

        if self.action == "retrieve":
            # Only the detail renders the role names. Prefetching them for
            # a list page that never shows them is a query for nothing.
            queryset = queryset.prefetch_related("groups")
        else:
            # `group_count` for the list serializer, as one aggregate in the
            # same query rather than a COUNT per row. `distinct=True` because
            # the filterset can join `groups` too, and a filtered join would
            # otherwise multiply the count.
            queryset = queryset.annotate(group_count=Count("groups", distinct=True))

        if is_student_only(user):
            return queryset.filter(id=user.id)
        # Superusers see everything; a school administrator their whole
        # school, including its school-level accounts (which hang off
        # `user.school` and have no institute); everyone else their own
        # institute. See `api/rbac/scoping.py` for why this is decided by
        # role and attachment rather than by `is_staff`.
        return scope_queryset(
            queryset,
            user,
            institute_path="institute",
            school_paths=("institute__school", "school"),
        )

    @extend_schema(
        request=ChangePasswordSerializer,
        responses={
            200: OpenApiResponse(
                response=None, description="Password changed successfully"
            ),
            400: OpenApiResponse(description="Validation error"),
        },
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[IsAuthenticated],
        url_path="change-password",
    )
    def change_password(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)

        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save()

        return Response(
            {"message": "Password updated successfully"},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Set another user's password",
        description=(
            "An administrator resets the password of an account in their "
            "scope - the person has forgotten theirs and cannot use "
            "`change-password`, which needs the current one.\n\n"
            "Scope is the same as the list: a superuser reaches any account, "
            "institute staff only the accounts of their own institute "
            "(anything else is a 404, not a 403, so the route does not confirm "
            "that an account exists outside the caller's institute). On top "
            "of that a caller may not reset their own password here, a "
            "superuser's, or that of anyone in a more senior role than "
            "their own.\n\n"
            "Every refresh token the account holds is blacklisted, so a "
            "session that was open somewhere else is ended too - if the "
            "reason for the reset was a compromised account, a reset that "
            "left the old session signed in would not have helped."
        ),
        request=AdminSetPasswordSerializer,
        responses={
            200: OpenApiResponse(response=None, description="Password set"),
            400: OpenApiResponse(description="Validation error"),
            403: OpenApiResponse(
                description="The target is outside what the caller may reset"
            ),
        },
    )
    @action(detail=True, methods=["post"], url_path="set-password")
    def set_password(self, request, pk=None):
        # `get_object` runs the scoped queryset, so an institute
        # administrator cannot even address an account outside their
        # institute - it does not exist as far as this route is concerned.
        target = self.get_object()
        actor = request.user

        refusal = _takeover_refusal(actor, target)
        if refusal:
            return Response({"detail": refusal}, status=status.HTTP_403_FORBIDDEN)

        serializer = AdminSetPasswordSerializer(
            data=request.data, context={"request": request, "target": target}
        )
        serializer.is_valid(raise_exception=True)

        target.set_password(serializer.validated_data["new_password"])
        target.save(update_fields=["password", "pepper_version"])

        revoked = _revoke_refresh_tokens(target)

        actor_type, actor_id = resolve_actor(actor)
        AuditLogService.log_action(
            system=AuditLog.System.PLATFORM,
            entity_type="UserPassword",
            entity_id=target.pk,
            action=AuditLog.Action.UPDATE,
            actor_type=actor_type,
            actor_id=actor_id,
            # Never the password, not even hashed. What the trail needs is
            # WHO reset WHOSE and that their sessions ended.
            old_values=None,
            new_values={"username": target.username, "sessions_revoked": revoked},
        )

        return Response(
            {
                "message": "Password set successfully",
                "username": target.username,
                "sessions_revoked": revoked,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Email a password reset code to another user",
        description=(
            "Starts the same one-time-code flow as the public "
            "`password-reset/request/` endpoint, for an account in the "
            "caller's scope. The code goes to the account's own email "
            "address and is never returned here; the person finishes the "
            "reset themselves on any of the platform's sign-in screens. Same "
            "scope and rank rules as `set-password`."
        ),
        request=None,
        responses={
            200: OpenApiResponse(response=None, description="Code sent"),
            400: OpenApiResponse(description="The account has no email or is deactivated"),
            403: OpenApiResponse(description="The target is outside what the caller may reset"),
        },
    )
    @action(detail=True, methods=["post"], url_path="send-password-reset")
    def send_password_reset(self, request, pk=None):
        target = self.get_object()
        actor = request.user

        refusal = _takeover_refusal(actor, target)
        if refusal:
            return Response({"detail": refusal}, status=status.HTTP_403_FORBIDDEN)

        if not target.email:
            return Response(
                {"detail": "This account has no email address to send a code to."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not target.is_active:
            return Response(
                {"detail": "This account is deactivated; reactivate it before resetting its password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_password_reset(target)

        actor_type, actor_id = resolve_actor(actor)
        AuditLogService.log_action(
            system=AuditLog.System.PLATFORM,
            entity_type="UserPassword",
            entity_id=target.pk,
            action=AuditLog.Action.UPDATE,
            actor_type=actor_type,
            actor_id=actor_id,
            old_values=None,
            new_values={"username": target.username, "reset_code_emailed": True},
        )

        return Response(
            {
                "detail": "A reset code has been emailed.",
                "email": mask_email(target.email),
                "expiry_minutes": OTP_EXPIRY_MINUTES,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Point an account at a new email address, or cancel a pending change",
        description=(
            "POST `{new_email}`: an administrator starts an email change for "
            "an account in their scope. A one-time confirmation link is mailed "
            "to the NEW address and nothing to the old one - this is for the "
            "person whose old mailbox is lost, for whom the self-service flow "
            "(a code to the current address first) can never complete. The "
            "account's email does not change until that link is opened; until "
            "then the detail endpoint reports it under `pending_email_change`. "
            "Posting again, with the same or another address, replaces the "
            "pending change with a fresh link - that is how a resend works.\n\n"
            "DELETE: cancel whatever change is pending; the emailed link stops "
            "working.\n\n"
            "Same scope and rank rules as `set-password`: only accounts in the "
            "caller's institute (or school, for a school administrator), never "
            "the caller's own, never a superuser's, and a school administrator's "
            "only by another school administrator."
        ),
        request=AdminEmailChangeSerializer,
        responses={
            200: OpenApiResponse(response=None, description="Link sent, or change cancelled"),
            400: OpenApiResponse(description="Address invalid, already used, or the account is deactivated"),
            403: OpenApiResponse(description="The target is outside what the caller may change"),
        },
    )
    @action(detail=True, methods=["post", "delete"], url_path="change-email")
    def change_email(self, request, pk=None):
        target = self.get_object()
        actor = request.user

        refusal = _takeover_refusal(actor, target)
        if refusal:
            return Response({"detail": refusal}, status=status.HTTP_403_FORBIDDEN)

        if request.method == "DELETE":
            cancelled = cancel_open_requests(target)
            if cancelled:
                _audit_email_change(actor, target, AuditLog.Action.CANCEL, {"pending_email": None})
            return Response(
                {
                    "detail": "Email change cancelled." if cancelled else "There was no email change in progress.",
                    "cancelled": bool(cancelled),
                },
                status=status.HTTP_200_OK,
            )

        if not target.is_active:
            return Response(
                {"detail": "This account is deactivated; reactivate it before changing its email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AdminEmailChangeSerializer(
            data=request.data, context={"request": request, "target": target}
        )
        serializer.is_valid(raise_exception=True)

        try:
            row = start_admin_request(
                target, serializer.validated_data["new_email"], actor=actor
            )
        except EmailChangeError as exc:
            # Field-keyed, so the dialog shows it under the address rather
            # than as a banner: the address is what needs changing.
            return Response({"new_email": [exc.message]}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "detail": "A confirmation link has been emailed to the new address.",
                "new_email": row.new_email,
                "current_email": target.email,
                "sent_at": row.confirm_sent_at,
                "expires_at": row.confirm_expires_at,
                "expires_in_minutes": CONFIRM_EXPIRY_MINUTES,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        summary="Move an account to a school or an institute",
        description=(
            "`{institute_id}` attaches the account to that institute (and to "
            "its school); `{school_id}` alone makes it school-level with no "
            "institute - what a school administrator's account looks like. "
            "Superusers may place any account anywhere; a school "
            "administrator only within their own school. Everything the "
            "account sees follows from this, so an account that shows empty "
            "lists is usually one that was never placed."
        ),
        request=SetScopeSerializer,
        responses={200: UserReadSerializer, 403: OpenApiResponse(description="Outside the caller's authority")},
    )
    @action(detail=True, methods=["post"], url_path="set-scope")
    def set_scope(self, request, pk=None):
        target = self.get_object()
        actor = request.user

        if not (actor.is_superuser or is_school_wide(actor)):
            return Response(
                {"detail": "Only a school administrator or a superuser may move an account between institutes."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if target.is_superuser and not actor.is_superuser:
            return Response(
                {"detail": "Only a superuser may move a superuser's account."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = SetScopeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        institute = serializer.validated_data.get("institute")
        school = serializer.validated_data.get("school")

        # The institute decides the school: an account cannot belong to an
        # institute of one school and to another school at the same time.
        if institute is not None:
            school = institute.school
        if school is None:
            return Response(
                {"detail": "Name an institute, or a school for a school-level account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not actor.is_superuser and school.pk != school_id_of(actor):
            return Response(
                {"detail": "You can only place accounts within your own school."},
                status=status.HTTP_403_FORBIDDEN,
            )

        before = {"institute": target.institute_id, "school": target.school_id}
        target.institute = institute
        target.school = school
        target.save(update_fields=["institute", "school"])

        actor_type, actor_id = resolve_actor(actor)
        AuditLogService.log_action(
            system=AuditLog.System.PLATFORM,
            entity_type="UserScope",
            entity_id=target.pk,
            action=AuditLog.Action.UPDATE,
            actor_type=actor_type,
            actor_id=actor_id,
            old_values=before,
            new_values={"institute": target.institute_id, "school": target.school_id},
        )

        target = self.get_queryset().get(pk=target.pk)
        return Response(UserReadSerializer(target, context={"request": request}).data)

    def perform_destroy(self, instance):
        # The avatar is the account's alone. Left behind on delete until now.
        remove_image(instance.profile, version=instance.cloudinary_version)
        super().perform_destroy(instance)

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer
        elif self.action == "retrieve":
            return UserReadSerializer
        elif self.action in ["update", "partial_update"]:
            return UserUpdateSerializer
        elif self.action == "destroy":
            return UserDeleteSerializer
        elif self.action == "change_password":
            return ChangePasswordSerializer
        elif self.action == "set_password":
            return AdminSetPasswordSerializer
        elif self.action == "change_email":
            return AdminEmailChangeSerializer
        return UserReadSlimSerializer

    def perform_create(self, serializer):
        # An account created by institute staff lands in their institute.
        # This used to pass `insitute=` (a typo Django accepted as an
        # unknown kwarg and then rejected at save), and the non-staff branch
        # returned the unbound `serializer.save` without calling it - so
        # neither branch ever created an account.
        user = self.request.user
        if user.is_staff and not user.is_superuser:
            serializer.save(institute=user.institute)
            return
        serializer.save()


def _audit_email_change(actor, target, action, new_values: dict) -> None:
    actor_type, actor_id = resolve_actor(actor)
    AuditLogService.log_action(
        system=AuditLog.System.PLATFORM,
        entity_type="UserEmail",
        entity_id=target.pk,
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        old_values=None,
        new_values={"username": target.username, **new_values},
    )


def _takeover_refusal(actor, target) -> str | None:
    """
    Why `actor` may not take over `target`'s account, or None if they may.

    Shared by the password reset, the emailed reset code and the email
    change: each of them hands whoever holds the result the account, so
    they are one authority with three shapes. The queryset already limits
    WHICH accounts are reachable. This decides whether a reachable one may
    be taken over, which is about rank: the delegation chain runs one way,
    so it must never point upward.
    """
    if actor.pk == target.pk:
        return "Use My account to change your own password or email."

    if actor.is_superuser:
        return None

    if target.is_superuser:
        return "Only a superuser may reset a superuser's password or change their email."

    # A school administrator outranks institute staff. An institute
    # administrator who could reset the school administrator's password
    # could then sign in as them - which is exactly the escalation the
    # chain exists to prevent.
    if _holds(target, Roles.SCHOOL_ADMIN) and not _holds(actor, Roles.SCHOOL_ADMIN):
        return (
            "Only a school administrator may reset another school "
            "administrator's password or change their email."
        )

    return None


def _holds(user, group_name: str) -> bool:
    return user.groups.filter(name=group_name).exists()


def _revoke_refresh_tokens(user) -> int:
    """
    Blacklist every outstanding refresh token for `user`. Returns how many.

    Access tokens live fifteen minutes and cannot be revoked; the refresh
    token is what keeps a session alive past that, so blacklisting them all
    is what "signed out everywhere" means here.
    """
    revoked = 0
    for outstanding in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=outstanding)
        if created:
            revoked += 1
    return revoked


class UserInviteView(APIView):
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = {"post": "users.invite_user"}

    @extend_schema(
        request=UserInviteSerializer,
        responses={201: None},
        description=(
            "Send an invitation email to a user. The message carries the "
            "activation link, the username the account will use, and a one-time "
            "password (unless INVITE_SEND_TEMPORARY_PASSWORD is off), branded "
            "with the inviting user's school."
        ),
    )
    def post(self, request):
        serializer = UserInviteSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        data = serializer.save()

        send_email_async(
            subject="Complete your account registration",
            to_email=data.email,
            template_name="user_invite",
            context=_invite_context(data, request.user),
            # Through the INVITER's mailbox: an institute officer sends as
            # the institute, a school administrator as the school, a
            # superuser as the platform. See core/services/email_sender.py.
            sender=email_sender(request.user),
        )

        return Response({"detail": "Invitation sent."}, status=201)


# mag add og thing nga if mag create og student e use ang institute id sa current user para if mag create man og admin alam na asa siya nga insti

# mag test sa diay daapat ko sa payments if e update dpat ma pending usab ang fee :>


class BulkUserInviteView(APIView):
    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = {"post": "users.bulk_invite_user"}

    @extend_schema(
        request=BulkUserInviteSerializer,
        responses={201: None},
        description="Send bulk student invitations",
    )
    def post(self, request):
        if request.user.institute is None:
            return Response(
                {"detail": "Your account is not attached to an institute, so it cannot invite students."},
                status=400,
            )

        serializer = BulkUserInviteSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)

        tokens = serializer.save()
        send_invitations_in_background(tokens, request.user)

        return Response(
            {"detail": f"Processing {len(tokens)} invitations in the background."},
            status=201,
        )


class StudentInviteFileView(APIView):
    """
    Bulk student invitations from a spreadsheet the students filled in.

    Same rules as the JSON list (`student_invites.validate_rows`), with two
    things a file needs that a pasted list does not:

      * `dry_run=true` - check every row and answer with the per-row result,
        sending nothing. The console shows this as a preview table before the
        administrator commits.
      * `skip_invalid=true` - send the valid rows and report the rest. Off by
        default, so a file with one typo is refused whole unless the caller
        has looked at the preview and chosen to go ahead without those rows.
    """

    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = {"post": "users.bulk_invite_user"}
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="Invite students from an uploaded spreadsheet",
        description=(
            "Multipart: `file` (.xlsx or .csv with `email` and optional "
            "`student_school_id` columns - download the template from "
            "`admin/student-bulk-invitations/template/`), `dry_run` and "
            "`skip_invalid` as `true`/`false`. Every row is validated: email "
            "format and uniqueness, student ID belongs to the caller's "
            "institute and has no account yet, no duplicates within the file. "
            "A dry run answers 200 with the rows and sends nothing; a real run "
            "answers 201 with the rows and `queued`, or 400 with the rows when "
            "some are invalid and `skip_invalid` is off."
        ),
        request={"multipart/form-data": {"type": "object", "properties": {
            "file": {"type": "string", "format": "binary"},
            "dry_run": {"type": "boolean"},
            "skip_invalid": {"type": "boolean"},
        }}},
        responses={200: None, 201: None, 400: OpenApiResponse(description="Unusable file or invalid rows")},
    )
    def post(self, request):
        institute = request.user.institute
        if institute is None:
            return Response(
                {"detail": "Your account is not attached to an institute, so it cannot invite students."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"file": ["No file provided."]}, status=status.HTTP_400_BAD_REQUEST)

        dry_run = _flag(request.data.get("dry_run"))
        skip_invalid = _flag(request.data.get("skip_invalid"))

        try:
            rows = parse_invite_file(uploaded)
        except ValueError as exc:
            return Response({"file": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

        validate_rows(rows, institute)
        valid = [row for row in rows if not row.errors]
        invalid = [row for row in rows if row.errors]

        payload = {
            "total_rows": len(rows),
            "valid_count": len(valid),
            "error_count": len(invalid),
            "queued": 0,
            "rows": [row.as_dict() for row in rows],
        }

        if dry_run:
            payload["detail"] = (
                f"{len(valid)} of {len(rows)} rows can be sent."
                if invalid
                else f"All {len(rows)} rows can be sent."
            )
            return Response(payload, status=status.HTTP_200_OK)

        if invalid and not skip_invalid:
            payload["detail"] = (
                f"{len(invalid)} of {len(rows)} rows have problems. Nothing was sent - fix the "
                "file, or resend with skip_invalid to leave those rows out."
            )
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)

        if not valid:
            payload["detail"] = "No row in the file can be sent."
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)

        try:
            tokens = create_student_invitations(valid, institute)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        send_invitations_in_background(tokens, request.user)

        payload["queued"] = len(tokens)
        payload["detail"] = f"Processing {len(tokens)} invitations in the background."
        return Response(payload, status=status.HTTP_201_CREATED)


class StudentInviteTemplateView(APIView):
    """The blank spreadsheet, for handing to students to fill in."""

    permission_classes = [IsAuthenticated, ActionPermission]
    required_permissions = {"get": "users.bulk_invite_user"}

    @extend_schema(
        summary="Download the student invitation spreadsheet template",
        responses={(200, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"): OpenApiTypes.BINARY},
    )
    def get(self, request):
        response = HttpResponse(
            render_template_workbook(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="student-invitations-template.xlsx"'
        return response


def _flag(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


# class RegisterView(APIView):
#     # authentication_classes = []
#     # permission_classes = []
#     @extend_schema(
#         request=RegisterSerializer,
#         responses={201: None},
#         description="Send an invitation email to a user",
#     )
#     def post(self, request):
#         serializer = RegisterSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)
#         serializer.save()

#         return Response({"detail": "Account created successfully."}, status=201)


class RegisterView(APIView):
    @extend_schema(
        summary="Activate User Account",
        description="Validates the activation token and renders a success or error HTML page.",
        parameters=[
            OpenApiParameter(
                name="token",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="The activation token sent via email.",
                required=True,
            ),
        ],
        responses={
            201: OpenApiTypes.STR,
            400: OpenApiTypes.STR,
        },
    )
    def get(self, request):
        token_value = request.query_params.get("token")

        if not token_value:
            return render(
                request,
                "email_templates/errors/activation_error.html",
                {
                    **email_brand(),
                    "message": "It looks like you're missing an activation token.",
                },
                status=400,
            )

        serializer = RegisterSerializer(data={"token": token_value})

        if serializer.is_valid():
            # Read before saving: activation clears the stored hash.
            registration_token = serializer.validated_data["registration_token"]  # type: ignore[index]
            had_password = bool(registration_token.initial_password)

            user = serializer.save()

            return render(
                request,
                "email_templates/account_created.html",
                {
                    **email_brand(user),
                    "username": user.username,
                    "email": user.email,
                    "has_temporary_password": had_password,
                    "login_url": getattr(settings, "FRONTEND_URL", None),
                },
                status=201,
            )
        else:
            return render(
                request,
                "email_templates/errors/activation_error.html",
                {
                    **email_brand(),
                    "message": "This link has expired or is no longer valid. Please try signing up again.",
                    "errors": serializer.errors,
                },
                status=400,
            )
