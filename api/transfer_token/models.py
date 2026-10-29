import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def generate_token():
    """64-char URL-safe random token. Not a JWT on purpose — this is an
    opaque, single-use, server-tracked handoff token, not a credential
    that needs to be self-describing."""
    return secrets.token_urlsafe(48)


# How long a transfer token is valid for, in seconds. Keep this short —
# it only needs to survive a redirect/API hop between your own systems.
TRANSFER_TOKEN_TTL_SECONDS = getattr(settings, "TRANSFER_TOKEN_TTL_SECONDS", 120)


class TransferToken(models.Model):
    """
    A short-lived, single-use token that lets an already-authenticated
    user "carry" their session from one system to another that shares
    the same user database, without logging in again.

    Lifecycle:
      1. System A (where the user is logged in) issues a TransferToken
         for that user.
      2. The opaque `token` value is handed to System B (via redirect,
         query param, header, whatever transport you choose).
      3. System B redeems the token once. On redemption we mark it used
         and issue a normal SimpleJWT access/refresh pair for that user.

    A token can only be redeemed once and only before it expires.
    """

    token = models.CharField(
        max_length=128,
        unique=True,
        default=generate_token,
        editable=False,
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transfer_tokens",
    )

    # Optional metadata — useful for auditing / restricting where a token
    # can be redeemed from. Not enforced by default, but there if you want it.
    issued_from = models.CharField(max_length=100, blank=True, default="")
    intended_for = models.CharField(max_length=100, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["token", "used_at", "expires_at"]),
        ]

    def __str__(self):
        return f"TransferToken(user={self.user_id}, used={self.is_used}, expired={self.is_expired})"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(
                seconds=TRANSFER_TOKEN_TTL_SECONDS
            )
        super().save(*args, **kwargs)

    @property
    def is_used(self):
        return self.used_at is not None

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_valid(self):
        return not self.is_used and not self.is_expired

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])