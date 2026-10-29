"""
The three SMTP sender columns a School and an Institute both carry.

    email_host_user               the Gmail (or other SMTP) account that sends
    email_host_password_encrypted its app password, encrypted (core/email_secrets.py)
    default_from_email            what the recipient sees as the From line;
                                  blank means "same as email_host_user"

The SMTP host, port and TLS flags stay in the environment: every tenant
authenticates against the same provider (Gmail, in practice) with its own
account, which is the situation an app password is for. Which row's
credentials a given message goes out with is decided by
core/services/email_sender.py.

A blank `email_host_user` means "not configured": whatever is stored beside
it is ignored, and the serializers clear the password whenever the user is
blanked so nothing lingers behind a switched-off sender.
"""

from __future__ import annotations

from django.db import models

from core.email_secrets import decrypt_secret, encrypt_secret

#: The columns to name in `update_fields` after touching the sender.
EMAIL_SENDER_FIELDS = (
    "email_host_user",
    "email_host_password_encrypted",
    "default_from_email",
)


class EmailSenderMixin(models.Model):
    email_host_user = models.EmailField(
        max_length=254, blank=True, default="", db_default=""
    )
    #: Fernet ciphertext, never the plaintext. Read it through
    #: `get_email_host_password()`; write it through `set_email_host_password()`.
    email_host_password_encrypted = models.TextField(blank=True, default="", db_default="")
    #: "Institute of Computing <registrar.ic@gmail.com>" or a bare address.
    default_from_email = models.CharField(
        max_length=254, blank=True, default="", db_default=""
    )

    class Meta:
        abstract = True

    # -- reading ------------------------------------------------------------

    @property
    def has_email_password(self) -> bool:
        return bool(self.email_host_password_encrypted)

    @property
    def email_sender_configured(self) -> bool:
        """Both halves present. Whether the password still *decrypts* is the sender's problem."""
        return bool(self.email_host_user) and self.has_email_password

    def get_email_host_password(self) -> str:
        """Plaintext, or ''. Raises `EmailSecretUnreadable` if the key is gone."""
        return decrypt_secret(self.email_host_password_encrypted)

    @property
    def effective_from_email(self) -> str:
        return self.default_from_email or self.email_host_user

    # -- writing ------------------------------------------------------------

    def set_email_host_password(self, raw_password: str | None) -> None:
        """Encrypt and store. Does not save."""
        self.email_host_password_encrypted = encrypt_secret(raw_password)

    def clear_email_sender(self) -> None:
        """Switch the sender off. Does not save."""
        self.email_host_user = ""
        self.email_host_password_encrypted = ""
        self.default_from_email = ""
