from typing import Optional

from rest_framework import serializers
from api.users.models import User
from api.institutes import serializers as InstituteSerializers

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from api.utils.cloudinary.cloudinary_utils import (
    current_version,
    upload_image,
    delete_file,
    get_file_url,
)
from api.utils.cloudinary.paths import Kind, folder_for, tags_for
from django.core.files.uploadedfile import UploadedFile

from django.contrib.auth.models import Group
from drf_spectacular.utils import extend_schema_field

from api.institutes.models import Institute
from api.rbac.roles import ROLE_DEFINITIONS, Roles, delegable_roles
from api.rbac.scoping import is_school_wide, school_id_of
from api.schools.models import School
from api.students.models import Student
from .models import UserRegistrationToken
from django.utils import timezone
import uuid
from api.schools.serializers import SchoolReadSlimSerializer
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.contrib.auth import get_user_model

from core import pepper

from .utils import generate_temporary_password, generate_username

User = get_user_model()

class UserCreateSerializer(serializers.ModelSerializer):
    """Schema for creating a user"""

    class Meta:
        model = User
        fields = [
            "username",
            "password",
            "email",
            "first_name",
            "last_name",
            # "institute",
        ]
        extra_kwargs = {
            "password": {"write_only": True},
        }

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User.objects.create_user(password=password, **validated_data)
        return user

    # def create(self, validated_data):
    #     image = validated_data.pop("profile")

    #     instance = super().create(validated_data)

    #     try:
    #         public_id = upload_image(image, folder=f"users/{instance.id}/")
    #         instance.profile = public_id
    #         instance.save()
    #     except ValueError as e:
    #         raise serializers.ValidationError({"profile": str(e)})


class GroupSlimSerializer(serializers.ModelSerializer):
    """One role, as the retrieve endpoint lists them."""

    class Meta:
        model = Group
        fields = ["id", "name"]
        read_only_fields = fields


class PendingEmailChangeSerializer(serializers.Serializer):
    """
    An email change waiting on its confirmation link, as the account detail
    reports it. The link itself is never here - only where it went, when,
    and who asked - so an administrator can see that the change is still
    pending and resend or cancel it.
    """

    new_email = serializers.EmailField()
    sent_at = serializers.DateTimeField(source="confirm_sent_at")
    expires_at = serializers.DateTimeField(source="confirm_expires_at")
    resends = serializers.IntegerField(source="confirm_resends")
    #: Display name of the administrator who started it; null when the
    #: account's owner started it themselves from My account.
    initiated_by = serializers.SerializerMethodField()

    def get_initiated_by(self, obj) -> str | None:
        actor = obj.initiated_by
        if actor is None:
            return None
        return (actor.get_full_name() or actor.username).strip() or None


class UserReadSerializer(serializers.ModelSerializer):
    """
    Detailed schema for retrieving ONE account (`GET /users/{id}/`).

    This is the only user serializer that renders roles. It used to send
    `group_id` / `group_name` taken from `obj.groups.first()`, which was a
    quiet lie: an account genuinely can hold several groups (a school
    administrator who also signs clearances), and "first" is decided by row
    id, so the role it reported changed depending on the order the groups
    were seeded in. The console then showed one role and the assign-roles
    dialog risked stripping the ones it could not see.

    `groups` is the whole set. The list endpoint deliberately does NOT carry
    it - see `UserReadSlimSerializer`.
    """

    institute = InstituteSerializers.InstituteReadSlimSerializer()
    school = SchoolReadSlimSerializer()
    profile = serializers.SerializerMethodField()
    groups = GroupSlimSerializer(many=True, read_only=True)
    group_count = serializers.SerializerMethodField()
    pending_email_change = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "institute",
            "school",
            "groups",
            "group_count",
            "pending_email_change",
            "is_superuser",
            "is_staff",
            "is_active",
            "profile",
            "date_joined",
            "last_login",
            "created_at",
            "updated_at",
            "updated_by",
        ]
        read_only_fields = fields

    def get_profile(self, obj) -> str:
        return get_file_url(obj.profile, version=obj.cloudinary_version)

    def get_group_count(self, obj) -> int:
        return _group_count(obj)

    @extend_schema_field(PendingEmailChangeSerializer(allow_null=True))
    def get_pending_email_change(self, obj):
        # One query, on the detail only. Worth it: "did the link go out, and
        # to where" is the first thing asked after starting a change.
        from api.change_email.services import pending_request

        row = pending_request(obj)
        return PendingEmailChangeSerializer(row).data if row else None


def _group_count(obj) -> int:
    """
    How many roles an account holds.

    `UserViewSet.get_queryset` annotates `group_count` so the list is one
    query rather than one per row. Anything else that renders these
    serializers (the student detail nests the linked account, for one) has no
    annotation, so fall back to counting - correct, just one query more.
    """
    annotated = getattr(obj, "group_count", None)
    if annotated is not None:
        return annotated
    return obj.groups.count()


class UserReadSlimSerializer(serializers.ModelSerializer):
    """
    Row schema for the list (`GET /users/`).

    No roles here - only how many there are. Rendering the group names on
    every row meant a prefetch of the M2M for a page of accounts, and the
    consumer (the console's account table) only ever displayed one of them
    anyway, so the column was both expensive and misleading. The count is
    enough to see who has no role yet, which is the thing the list is scanned
    for; the full set is one click away on the retrieve endpoint.
    """

    institute = InstituteSerializers.InstituteReadSlimSerializer()
    school = SchoolReadSlimSerializer()
    profile = serializers.SerializerMethodField()
    group_count = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "institute",
            "school",
            "group_count",
            "is_active",
            "profile",
        ]
        read_only_fields = fields

    def get_profile(self, obj) -> str:
        return get_file_url(obj.profile, version=obj.cloudinary_version)

    def get_group_count(self, obj) -> int:
        return _group_count(obj)


class UserReadMiniSerializer(serializers.ModelSerializer):
    """Shortest schema for reading/retrieving"""

    institute_name = serializers.CharField(source="institute.institute_name")
    # Declared, not just listed in `fields`: a name in `Meta.fields` with no
    # model column and no declared field raises ImproperlyConfigured the
    # first time the serializer is instantiated, which is how this class sat
    # broken for as long as nothing used it.
    group_count = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "institute_name",
            "username",
            "email",
            "first_name",
            "last_name",
            "group_count",
        ]
        read_only_fields = fields

    def get_group_count(self, obj) -> int:
        return _group_count(obj)


class UserUpdateSerializer(serializers.ModelSerializer):
    """Schema for updates"""

    profile = serializers.ImageField(required=False, allow_null=True, write_only=True)
    remove_profile = serializers.BooleanField(
        required=False, default=False, write_only=True
    )
    profile_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "username",
            "password",
            # "email",
            "first_name",
            "last_name",
            # "status",
            # "institute",
            # "is_active",
            "profile",
            "remove_profile",
            "profile_url",
        ]
        read_only_fields = ["profile_url"]
        
        extra_kwargs = {
            "password": {"write_only": True, "required": False}
        }

    def validate_password(self, value):
        """
        Field-level validation for the password.
        Runs Django's built-in password validators.
        """
        try:
            validate_password(value, user=self.instance)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def validate(self, attrs):
        if attrs.get("remove_profile") and attrs.get("profile"):
            raise serializers.ValidationError(
                {
                    "profile": "Cannot upload a new profile image while simultaneously requesting its removal."
                }
            )
        return attrs

    def get_profile_url(self, obj) -> str:
        return get_file_url(obj.profile, version=obj.cloudinary_version)

    def update(self, instance, validated_data):
        remove_profile = validated_data.pop("remove_profile", False)
        image = validated_data.pop("profile", None)
        password = validated_data.pop("password", None)

        instance = super().update(instance, validated_data)

        if password:
            instance.set_password(password)
            instance.save() 

        if remove_profile:
            if instance.profile:
                delete_file(instance.profile, version=instance.cloudinary_version)
            instance.profile = None
            instance.cloudinary_version = None
            instance.save()

        elif image and isinstance(image, UploadedFile):
            old_profile = instance.profile
            old_version = instance.cloudinary_version
            try:
                public_id = upload_image(
                    image, folder=folder_for(instance, Kind.PROFILES), tags=tags_for(instance)
                )
                instance.profile = public_id
                instance.cloudinary_version = current_version()
                instance.save()

                if old_profile:
                    delete_file(old_profile, version=old_version)

            except ValueError as e:
                raise serializers.ValidationError({"profile": str(e)})

        return instance

class UserDeleteSerializer(serializers.ModelSerializer):
    """Schema for confirming deletion"""

    class Meta:
        model = User
        fields = ["id", "username", "email"]


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_current_password(self, value):
        user = self.context["request"].user

        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")

        return value

    def validate(self, data): # type: ignore
        user = self.context["request"].user
        new_password = data["new_password"]
        confirm_password = data["confirm_password"]

        if new_password != confirm_password:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )

        if user.check_password(new_password):
            raise serializers.ValidationError(
                {
                    "new_password": "New password must be different from the current password."
                }
            )

        try:
            validate_password(new_password, user=user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password": e.messages})

        return data


class AdminSetPasswordSerializer(serializers.Serializer):
    """
    An administrator setting somebody ELSE's password.

    `POST /users/{id}/set-password/`. Distinct from `ChangePasswordSerializer`
    on purpose: that one proves you know the current password, which is the
    whole point of it, and this one exists precisely because the owner does
    not - a student or an officer who has forgotten theirs and asked the
    institute administrator to reset it.

    The target comes in through the context, not the request body, so the
    password validators run against the account being changed (a password
    that contains the *target's* username is the one to refuse, not one
    containing the administrator's).
    """

    new_password = serializers.CharField(write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, data):  # type: ignore[override]
        target = self.context["target"]

        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Passwords do not match."}
            )

        try:
            validate_password(data["new_password"], user=target)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": exc.messages})

        return data


class AdminEmailChangeSerializer(serializers.Serializer):
    """
    An administrator pointing somebody ELSE's account at a new address.

    `POST /users/{id}/change-email/`. The target comes through the context so
    "already the email on this account" is judged against theirs. The
    uniqueness rule is the platform's usual one and is re-checked when the
    link is opened - the address could be claimed in between.
    """

    new_email = serializers.EmailField()

    def validate_new_email(self, value):
        email = value.lower().strip()
        target = self.context["target"]

        if target.email and email == target.email.lower().strip():
            raise serializers.ValidationError("That is already the email on this account.")

        if User.objects.filter(email__iexact=email).exclude(pk=target.pk).exists():
            raise serializers.ValidationError(
                "That email address is already used by another account."
            )

        return email


class SetScopeSerializer(serializers.Serializer):
    """Where an account belongs - see `UserViewSet.set_scope` for the rules."""

    school_id = serializers.PrimaryKeyRelatedField(
        queryset=School.objects.all(), source="school", required=False, allow_null=True
    )
    institute_id = serializers.PrimaryKeyRelatedField(
        queryset=Institute.objects.all(), source="institute", required=False, allow_null=True
    )


def build_registration_token(
    *, email, group, institute, school=None, student=None, reserved_usernames=None
) -> UserRegistrationToken:
    """
    An unsaved invitation, with the username and one-time password the
    invitation email will quote.

    The plaintext password is hung on the instance as `raw_password` and is
    never persisted: the caller mails it, and what reaches the database is
    the hash the account will be created with. Nowhere else can read it back
    - if the invitation is lost, the answer is a new invitation, not a
    lookup.
    """
    expiry_days = getattr(settings, "INVITE_EXPIRY_DAYS", 3)
    send_password = getattr(settings, "INVITE_SEND_TEMPORARY_PASSWORD", True)

    raw_password = generate_temporary_password() if send_password else ""

    token = UserRegistrationToken(
        email=email,
        group=group,
        institute=institute,
        # An institute-level account belongs to its institute's school; a
        # school-level one names the school directly and has no institute.
        school=school or getattr(institute, "school", None),
        student=student,
        username=generate_username(email, taken=reserved_usernames),
        initial_password=(
            make_password(pepper.pepper_password(raw_password, pepper.current_version()))
            if raw_password
            else ""
        ),
        initial_password_pepper_version=pepper.current_version() if raw_password else 0,
        token=uuid.uuid4().hex,
        expires_at=timezone.now() + timezone.timedelta(days=expiry_days),
    )
    token.raw_password = raw_password
    return token


class UserInviteSerializer(serializers.Serializer):
    """
    One invitation, placed somewhere in the org chart.

    ## Who may invite whom

    `group_id` is checked against `delegable_roles(request.user)` - the
    `DELEGATION` table in `api/rbac/roles.py`. Before that check the
    queryset was every group in the database, so anyone holding
    `users.invite_user` (an institute-level grant) could post
    `group_id=<School Admins>` and mint an account two levels above their
    own. The console had the same table on its side and could only hide the
    option; this is what closes it. The refusal names what the caller MAY
    invite, because DRF's own "invalid pk" would send them looking for a
    seeding problem that does not exist.

    ## Where the account lands

    The token records the school and institute the account will belong to,
    and `RegisterSerializer` copies them onto the account at activation:

        School Admins    `school_id` names the school. Superusers only - a
                         school administrator never appoints a peer. The
                         account is school-wide and has no institute; the
                         school is what `api/rbac/scoping.py` reads.
        anything else    `institute_id` names the institute. A school
                         administrator picks one in their own school; an
                         institute administrator's own institute is used,
                         and naming a different one is refused rather than
                         silently replaced.
    """

    email = serializers.EmailField()
    group_id = serializers.PrimaryKeyRelatedField(
        queryset=Group.objects.all(), source="group"
    )
    school_id = serializers.PrimaryKeyRelatedField(
        queryset=School.objects.all(), source="school", required=False, allow_null=True
    )
    institute_id = serializers.PrimaryKeyRelatedField(
        queryset=Institute.objects.all(),
        source="institute",
        required=False,
        allow_null=True,
    )
    student_school_id = serializers.CharField(required=False, allow_blank=True)

    def validate_email(self, value):
        email = value.lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                "This email address already belongs to an account."
            )
        return email

    def validate_group_id(self, group):
        allowed = delegable_roles(self.context["request"].user)
        if group.name not in allowed:
            offer = ", ".join(sorted(allowed)) or "nobody"
            raise serializers.ValidationError(
                f"Your role does not appoint {group.name}. You may invite: {offer}."
            )
        return group

    def validate_student_school_id(self, value):
        value = (value or "").strip()
        if not value:
            return None
        try:
            return Student.objects.get(
                s_studentID=value,
                program__institute=self.context["request"].user.institute,
            )
        except Student.DoesNotExist:
            raise serializers.ValidationError(
                "Invalid student School ID or Student Is Outside the Assigned Institute"
            )

    def validate(self, attrs):
        user = self.context["request"].user
        group = attrs["group"]
        school = attrs.pop("school", None)
        institute = attrs.pop("institute", None)

        if group.name == Roles.SCHOOL_ADMIN:
            if school is None:
                raise serializers.ValidationError(
                    {"school_id": "Choose the school this administrator will run."}
                )
            attrs["school"], attrs["institute"] = school, None
            return attrs

        if user.is_superuser:
            if institute is None:
                raise serializers.ValidationError(
                    {"institute_id": "Choose the institute this person will belong to."}
                )
        elif is_school_wide(user):
            if institute is None:
                raise serializers.ValidationError(
                    {"institute_id": "Choose which of your school's institutes this person will belong to."}
                )
            own_school = school_id_of(user)
            if own_school is None or institute.school_id != own_school:
                raise serializers.ValidationError(
                    {"institute_id": "That institute is not in your school."}
                )
        else:
            if user.institute_id is None:
                raise serializers.ValidationError(
                    {"institute_id": "Your account is not attached to an institute, so it cannot invite anyone into one."}
                )
            if institute is not None and institute.pk != user.institute_id:
                raise serializers.ValidationError(
                    {"institute_id": "You can only invite people into your own institute."}
                )
            institute = user.institute

        attrs["institute"] = institute
        attrs["school"] = institute.school
        return attrs

    def create(self, validated_data):
        student = validated_data.pop("student_school_id", None)

        token = build_registration_token(
            email=validated_data["email"],
            group=validated_data["group"],
            institute=validated_data["institute"],
            school=validated_data["school"],
            student=student,
        )
        token.save()
        return token


class StudentInviteItemSerializer(serializers.Serializer):
    """
    Shape only. The rules - institute scoping, "already has an account",
    duplicates within the batch - are in `student_invites.validate_rows`,
    shared with the spreadsheet upload so both doors agree. Field-level
    validation here used to look the student up WITHOUT the institute,
    which let an administrator invite (and so learn about) a student of
    another institute.
    """

    email = serializers.EmailField()
    student_school_id = serializers.CharField(required=False, allow_blank=True)


class BulkUserInviteSerializer(serializers.Serializer):
    students = StudentInviteItemSerializer(many=True)

    def validate(self, attrs):
        from .student_invites import InviteRow, validate_rows

        request = self.context.get("request")
        institute = getattr(getattr(request, "user", None), "institute", None)
        if institute is None:
            raise serializers.ValidationError(
                {"detail": "Your account is not attached to an institute, so it cannot invite students."}
            )

        rows = [
            InviteRow(
                row=index + 1,
                email=item["email"],
                student_school_id=item.get("student_school_id") or "",
            )
            for index, item in enumerate(attrs["students"])
        ]
        validate_rows(rows, institute)

        problems = [
            f"Row {row.row} ({row.email or 'no email'}): {' '.join(row.errors)}"
            for row in rows
            if row.errors
        ]
        if problems:
            # Field-keyed on `students`, so the console shows it under the
            # list rather than as a bare banner. Nothing is sent: a partial
            # send with no record of which half went is much harder to
            # recover from than a rejected batch.
            raise serializers.ValidationError({"students": problems})

        attrs["_rows"] = rows
        return attrs

    def create(self, validated_data):
        from .student_invites import create_student_invitations

        request = self.context.get("request")
        try:
            return create_student_invitations(validated_data["_rows"], request.user.institute)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))


class RegisterSerializer(serializers.Serializer):
    token = serializers.CharField()

    def validate(self, attrs):
        try:
            token = UserRegistrationToken.objects.select_related(
                "group", "student"
            ).get(token=attrs["token"])
        except UserRegistrationToken.DoesNotExist:
            raise serializers.ValidationError("Invalid token.")

        if not token.is_valid():
            raise serializers.ValidationError("Token expired or already used.")

        if User.objects.filter(email=token.email).exists():
            raise serializers.ValidationError("User already exists.")

        attrs["registration_token"] = token
        return attrs

    def create(self, validated_data):
        token = validated_data["registration_token"]

        # The username was reserved when the invitation went out, so the
        # email could quote it. Re-check it anyway: days may have passed,
        # and someone else may have taken it in the meantime.
        username = token.username or generate_username(token.email)
        if User.objects.filter(username__iexact=username).exists():
            username = generate_username(token.email)

        user = User.objects.create_user(
            username=username, email=token.email, password=None
        )

        if token.initial_password:
            # Already hashed at invite time - assign, never re-hash. The
            # version travels with it; a sign-in under an older pepper than
            # the current one re-hashes the account like any other.
            user.password = token.initial_password
            user.pepper_version = token.initial_password_pepper_version
        else:
            user.set_unusable_password()

        user.groups.add(token.group)
        user.email_verified_at = timezone.now()# type: ignore
        # Where the invitation placed them. An institute-level token carries
        # both; a School Admins token carries the school alone, and that
        # account stays without an institute on purpose.
        user.institute = token.institute # type: ignore
        user.school = token.school or getattr(token.institute, "school", None)  # type: ignore
        # The flag several viewsets still branch on. `assign_role` sets it
        # for seeded and re-assigned accounts; an INVITED administrator
        # never went through that, so until now they activated with
        # `is_staff` off and every scoped list came back empty for them.
        spec = ROLE_DEFINITIONS.get(token.group.name)
        if spec is not None and spec.staff_flag:
            user.is_staff = True
        user.save()

        if token.student:
            token.student.user = user
            token.student.save()

        token.used_at = timezone.now()
        # The hash has done its job; the account owns it now.
        token.initial_password = ""
        token.username = username
        token.save(update_fields=["used_at", "initial_password", "username"])

        return user
