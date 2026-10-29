from django.db import models
from django.utils import timezone


class PasswordResetRequest(models.Model):
    """
    One row per 'forgot password' attempt. Stores a HASH of the OTP code,
    never the code itself.
    """

    email = models.EmailField(db_index=True)
    code_hash = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    validated_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)

    class Meta:
        indexes = [
            models.Index(fields=["email", "created_at"]),
        ]

    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    def is_validated(self) -> bool:
        return self.validated_at is not None

    def is_locked(self) -> bool:
        return self.attempts >= self.max_attempts

    def __str__(self):
        return f"PasswordResetRequest({self.email}, created={self.created_at})"


class PasswordChangeToken(models.Model):
    """
    Issued only AFTER a PasswordResetRequest is successfully verified.
    This is what the client actually uses to set the new password.
    """

    reset_request = models.ForeignKey(
        PasswordResetRequest, on_delete=models.CASCADE, related_name="change_tokens"
    )
    email = models.EmailField(db_index=True)
    token_hash = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    def is_used(self) -> bool:
        return self.used_at is not None

    def __str__(self):
        return f"PasswordChangeToken({self.email}, used={self.is_used()})"