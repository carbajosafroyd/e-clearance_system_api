from django.contrib.auth.hashers import check_password as _check_hash
from django.contrib.auth.models import AbstractUser, Group
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from api.institutes.models import Institute
from core.BaseModel import ActorStampedModel
from core import pepper
from config import settings
from api.students.models import Student
import uuid
from django.utils import timezone


class UserManager(DjangoUserManager):
    """
    Django's own `create_user` writes `make_password(password)` straight onto
    the row and never calls `set_password`, so without this the seeders and
    `createsuperuser` would store bare, version-0 hashes that only pick up
    the pepper on the account's first sign-in.
    """

    def _create_user_object(self, username, email, password, **extra_fields):
        user = super()._create_user_object(username, email, None, **extra_fields)
        if password is not None:
            user.set_password(password)
        return user


class User(AbstractUser, ActorStampedModel):
    objects = UserManager()

    # class UserStatus(models.TextChoices):
    #     ACTIVE = "active", "Active"
    #     INACTIVE = "inactive", "Inactive"
    #     SUSPENDED = "suspended", "Suspended"

    # status = models.CharField(
    #     max_length=20, choices=UserStatus.choices, default=UserStatus.ACTIVE
    # )
    institute = models.ForeignKey(
        "institutes.Institute",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    profile = models.CharField(max_length=100, null=True)
    #: The Cloudinary account version the profile picture was uploaded under - see
    #: CLOUDINARY_ACCOUNTS in settings. Null means "not known yet": the
    #: helpers look it up across accounts on first use.
    cloudinary_version = models.PositiveSmallIntegerField(null=True, blank=True)
    #: Which PASSWORD_PEPPER_<N> the stored hash was made with - see
    #: core/pepper.py. 0 is a hash from before the pepper existed; both are
    #: verified as they are and re-hashed under the current version on the
    #: next successful sign-in.
    pepper_version = models.PositiveSmallIntegerField(default=0)
    email_verified_at = models.DateTimeField(null=True)
    school = models.ForeignKey(
        "schools.School",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )

    class Meta:
        db_table = "users"
        indexes = [models.Index(fields=["institute", "is_active"])]
        permissions = [
            ("invite_user", "Can invite a single user"),
            ("bulk_invite_user", "Can invite users in bulk"),
            ("assign_user_role", "Can change which roles a user holds"),
            # Resetting somebody else's password without knowing the current
            # one. Separate from `change_user` because students hold that for
            # their own profile - see UserViewSet.set_password.
            ("set_user_password", "Can set another user's password"),
            # Start an email change for somebody else - a confirmation link
            # to the NEW address, nothing to the old one. For the account
            # whose mailbox is lost; see UserViewSet.change_email.
            ("change_user_email", "Can start an email change for another user"),
        ]

    def __str__(self):
        return self.username

    # -- password pepper ----------------------------------------------------
    # Every path that sets or checks a password funnels through these two -
    # ModelBackend (so the JWT login and the admin), the change-password
    # flows, `createsuperuser`, the seeders via UserManager above. Only the
    # invitation copies a ready-made hash onto the row, and it carries the
    # version along with it.

    def set_password(self, raw_password):
        if raw_password is None:
            # Django's spelling of "no usable password" - nothing to pepper.
            super().set_password(None)
            return
        version = pepper.current_version()
        super().set_password(pepper.pepper_password(raw_password, version))
        # AbstractBaseUser stashes what it was given so save() can run the
        # password_changed() validators; hand them the password, not the digest.
        self._password = raw_password
        self.pepper_version = version

    def check_password(self, raw_password):
        version = self.pepper_version
        try:
            candidate = pepper.pepper_password(raw_password, version)
        except pepper.PepperUnavailable:
            # The pepper this hash needs is gone from the environment. Not an
            # error to surface on a login form - a plain "wrong password" -
            # but the account can only come back through a reset.
            return False

        def rehash():
            # This is the one moment the plaintext is in our hands, so it is
            # the one moment the row can move to the current pepper.
            self.set_password(raw_password)
            self._password = None
            self.save(update_fields=["password", "pepper_version"])

        upgraded = False

        def setter(_peppered):
            # Django asks for a re-hash when the hasher itself moved on (more
            # iterations, a new algorithm). Do it from the raw password, not
            # the peppered value it hands us, or the pepper would be applied
            # twice.
            nonlocal upgraded
            upgraded = True
            rehash()

        ok = _check_hash(candidate, self.password, setter)
        if ok and not upgraded and version != pepper.current_version():
            rehash()
        return ok

    def save(self, *args, **kwargs):
        # `set_password` changes two fields, and every caller remembered the
        # second until the ones that did not: the user seeder and both admin
        # reset flows saved with update_fields=["password"], leaving rows
        # whose hash was peppered under the current version but still
        # stamped 0 - so check_password fed the hasher the raw password and
        # nobody could sign in to those accounts. The pair travels together
        # from here on, whatever the caller listed.
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "password" in update_fields:
            kwargs["update_fields"] = set(update_fields) | {"pepper_version"}
        return super().save(*args, **kwargs)


class OAuthAccount(models.Model):
    PROVIDERS = (
        ("google", "Google"),
        ("microsoft", "Microsoft"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_accounts",
    )

    provider = models.CharField(max_length=20, choices=PROVIDERS)
    provider_user_id = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "oauth_accounts"
        unique_together = ("provider", "provider_user_id")

    def __str__(self):
        return f"{self.user.email} ({self.provider})"


class UserRegistrationToken(models.Model):
    email = models.EmailField()

    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        help_text="Group assigned on successful registration",
    )

    # Where the account will belong once it is activated. An institute-level
    # invitation names the institute (and its school); a School Admins
    # invitation names only the school - the account is school-wide and has
    # no institute, which is why `institute` had to stop being required.
    institute = models.ForeignKey(
        Institute, null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    school = models.ForeignKey(
        "schools.School", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )

    student = models.ForeignKey(
        Student, null=True, blank=True, on_delete=models.CASCADE
    )

    token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    # Reserved when the invitation is sent, because the invitation email has
    # to be able to name it: sign-in is by username, not by email.
    username = models.CharField(max_length=150, blank=True, default="")

    # The one-time password mailed with the invitation, stored the way any
    # password is - hashed. Copied onto the account at activation, so the
    # plaintext exists only in the message that was sent, never here.
    initial_password = models.CharField(max_length=128, blank=True, default="")
    #: The pepper version `initial_password` was hashed with, copied onto the
    #: account together with the hash. The invitation may be days old by the
    #: time it is opened - a rotation in between must not break it.
    initial_password_pepper_version = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_registration_tokens"
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["used_at", "expires_at"]),
        ]
    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now()

    def __str__(self):
        return f"Registration token for {self.email}"
