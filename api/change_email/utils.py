import hashlib
import random
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from core.services.links import backend_link


OTP_LENGTH = getattr(settings, "EMAIL_CHANGE_OTP_LENGTH", 6)
OTP_EXPIRY_MINUTES = getattr(settings, "EMAIL_CHANGE_OTP_EXPIRY_MINUTES", 10)
CHANGE_TOKEN_EXPIRY_MINUTES = getattr(
    settings, "EMAIL_CHANGE_TOKEN_EXPIRY_MINUTES", 15
)
CONFIRM_EXPIRY_MINUTES = getattr(settings, "EMAIL_CHANGE_CONFIRM_EXPIRY_MINUTES", 60)
MAX_OTP_ATTEMPTS = getattr(settings, "EMAIL_CHANGE_MAX_ATTEMPTS", 5)


def generate_otp_code() -> str:
    """Zero-padded numeric code, e.g. '048213'."""
    return "".join(
        random.SystemRandom().choice("0123456789") for _ in range(OTP_LENGTH)
    )


def generate_token() -> str:
    """URL-safe random token — used both for the change token and the link."""
    return secrets.token_urlsafe(32)


def hash_value(value: str) -> str:
    """
    Deterministic hash for short-lived, high-entropy secrets (OTP codes and
    tokens), so a database dump never hands out a live code. Not for
    passwords — those go through Django's hashers.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def otp_expiry():
    return timezone.now() + timedelta(minutes=OTP_EXPIRY_MINUTES)


def change_token_expiry():
    return timezone.now() + timedelta(minutes=CHANGE_TOKEN_EXPIRY_MINUTES)


def confirm_expiry():
    return timezone.now() + timedelta(minutes=CONFIRM_EXPIRY_MINUTES)


def mask_email(email: str) -> str:
    """
    's.dela.cruz@gmail.com' -> 's••••••••z@gmail.com'.

    The step-1 response echoes where the code went without spelling out an
    address the caller might not already know.
    """
    if not email or "@" not in email:
        return email or ""

    bullet = "•"
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        return f"{local[:1]}{bullet}@{domain}"
    return f"{local[0]}{bullet * (len(local) - 2)}{local[-1]}@{domain}"


def confirmation_link(token: str) -> str:
    """Absolute URL for the link mailed to the new address."""
    return backend_link("/api/v1/email-change/confirm/", token=token)


def format_moment(moment) -> str:
    """A timestamp a reader can place, in the deployment's timezone."""
    return timezone.localtime(moment).strftime("%d %b %Y, %I:%M %p")
