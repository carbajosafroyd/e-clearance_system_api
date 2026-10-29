"""
The sender fields on the School and Institute serializers.

Read side (`EmailSenderReadMixin`)

    email_host_user           the address that sends
    default_from_email        the From line, '' = same as the address
    has_email_password        an app password is on file (never the password)
    email_sender_configured   both halves present - mail goes out as this row
    effective_email_sender    which mailbox this row's mail ACTUALLY goes out
                              through right now - its own, or what it falls
                              back to (an institute -> its school -> the
                              platform): {source, from_email, label}. So the
                              console can show "leave this empty and mail is
                              sent as X" with the real X.

Write side (`EmailSenderWriteMixin`, on top of the read side)

    email_host_password       write-only. Omitted or blank = keep what is
                              stored; anything else replaces it, encrypted.

The rules, enforced in `validate` so the console shows them under the
right field:

  * a password needs an address to belong to;
  * an address needs a password - a stored one, or one in this request;
  * a *changed* address needs a fresh password: an app password is issued
    per Google account, so the old one cannot possibly work for the new
    address, and carrying it across would fail on the first send;
  * blanking the address switches the sender off and drops the stored
    password with it, so nothing lingers behind a sender that is "off".

`create`/`update` pop the password before the model ever sees it and
encrypt it after the row is saved. Stack this mixin with the Cloudinary
image mixins in either order - each pops its own fields and delegates.
"""

from __future__ import annotations

from email.utils import parseaddr

from django.core.validators import EmailValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from core.services.email_sender import sender_for

#: Google app passwords are 16 letters, often pasted with their spaces;
#: other providers vary. Generous, but not unbounded.
MAX_PASSWORD_LENGTH = 200

EMAIL_SENDER_READ_FIELDS = (
    "email_host_user",
    "default_from_email",
    "has_email_password",
    "email_sender_configured",
    "effective_email_sender",
)
EMAIL_SENDER_WRITE_FIELDS = EMAIL_SENDER_READ_FIELDS + ("email_host_password",)


class EmailSenderReadMixin(serializers.Serializer):
    has_email_password = serializers.BooleanField(read_only=True)
    email_sender_configured = serializers.BooleanField(read_only=True)
    effective_email_sender = serializers.SerializerMethodField()

    def get_effective_email_sender(self, obj) -> dict:
        # An Institute hangs off a school; a School does not. The chain is
        # the same one the mail itself uses (core/services/email_sender.py).
        if hasattr(obj, "school_id"):
            return sender_for(institute=obj).public()
        return sender_for(school=obj).public()


class EmailSenderWriteMixin(EmailSenderReadMixin):
    email_host_password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        trim_whitespace=False,
        max_length=MAX_PASSWORD_LENGTH,
        help_text=(
            "The app password for email_host_user. Omit or send blank to keep "
            "the stored one. Never returned."
        ),
    )

    # -- field-level ---------------------------------------------------------

    def validate_email_host_user(self, value: str) -> str:
        return (value or "").strip().lower()

    def validate_default_from_email(self, value: str) -> str:
        """
        A bare address or `Display Name <address>`. The address part has to
        be a real one - Gmail rejects a From it cannot map to the account or
        one of its verified aliases, and that error surfaces as a silently
        dropped invitation days later.
        """
        value = (value or "").strip()
        if not value:
            return ""
        _name, address = parseaddr(value)
        if not address:
            raise serializers.ValidationError(
                'Enter an email address, or "Display Name <address>".'
            )
        try:
            EmailValidator()(address)
        except DjangoValidationError:
            raise serializers.ValidationError(f'"{address}" is not a valid email address.')
        return value

    def validate_email_host_password(self, value: str) -> str:
        # Google shows app passwords as "abcd efgh ijkl mnop"; the spaces
        # are cosmetic and SMTP does not want them.
        value = value or ""
        return value.replace(" ", "") if len(value.replace(" ", "")) == 16 else value.strip()

    # -- object-level --------------------------------------------------------

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = self.instance

        stored_user = getattr(instance, "email_host_user", "") or ""
        has_stored_password = bool(getattr(instance, "has_email_password", False))

        host_user = attrs.get("email_host_user", stored_user)
        new_password = attrs.get("email_host_password", "")
        errors = {}

        if new_password and not host_user:
            errors["email_host_user"] = (
                "Enter the sender email address this app password belongs to."
            )
        elif host_user and not new_password:
            if not has_stored_password:
                errors["email_host_password"] = (
                    "Enter the app password for this sender email."
                )
            elif host_user != stored_user:
                errors["email_host_password"] = (
                    "The sender address changed. App passwords are issued per "
                    "account, so enter the one for the new address."
                )

        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    # -- persistence ---------------------------------------------------------

    def _pop_password(self, validated_data) -> str | None:
        return validated_data.pop("email_host_password", None) or None

    def _apply_password(self, instance, password: str | None) -> None:
        changed: list[str] = []
        if not instance.email_host_user:
            # Off. Drop whatever is stored so a later "on" cannot silently
            # reuse a password meant for a different address.
            if instance.email_host_password_encrypted or instance.default_from_email:
                instance.clear_email_sender()
                changed += ["email_host_password_encrypted", "default_from_email"]
        elif password:
            instance.set_email_host_password(password)
            changed.append("email_host_password_encrypted")
        if changed:
            instance.save(update_fields=changed)

    def create(self, validated_data):
        password = self._pop_password(validated_data)
        instance = super().create(validated_data)
        self._apply_password(instance, password)
        return instance

    def update(self, instance, validated_data):
        password = self._pop_password(validated_data)
        instance = super().update(instance, validated_data)
        self._apply_password(instance, password)
        return instance
