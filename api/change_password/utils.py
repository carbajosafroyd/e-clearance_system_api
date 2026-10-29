import hashlib
import random
import secrets

from django.conf import settings
from django.utils import timezone
from datetime import timedelta


OTP_LENGTH = getattr(settings, "PASSWORD_RESET_OTP_LENGTH", 6)
OTP_EXPIRY_MINUTES = getattr(settings, "PASSWORD_RESET_OTP_EXPIRY_MINUTES", 10)
CHANGE_TOKEN_EXPIRY_MINUTES = getattr(
    settings, "PASSWORD_RESET_CHANGE_TOKEN_EXPIRY_MINUTES", 10
)
MAX_OTP_ATTEMPTS = getattr(settings, "PASSWORD_RESET_MAX_ATTEMPTS", 5)


def generate_otp_code() -> str:
    """Generates a zero-padded numeric code, e.g. '048213'."""
    return "".join(random.SystemRandom().choice("0123456789") for _ in range(OTP_LENGTH))


def generate_change_token() -> str:
    """URL-safe random token handed to the client after verification."""
    return secrets.token_urlsafe(32)


def hash_value(value: str) -> str:
    """
    Simple, fast, deterministic hash for short-lived, high-entropy-ish
    secrets (OTP codes / tokens). This is NOT for user passwords —
    Django's password hashers (used via user.set_password) already
    handle those correctly with proper salting/slow hashing.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def otp_expiry():
    return timezone.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)


def change_token_expiry():
    return timezone.now() + timedelta(minutes=CHANGE_TOKEN_EXPIRY_MINUTES)