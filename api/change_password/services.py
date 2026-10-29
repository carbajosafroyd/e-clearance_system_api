"""
Starting a password reset for an account.

Split out of `RequestPasswordResetView` so the same code path serves two
callers: the anonymous "forgot password" form, and an administrator who
clicks "email a reset code" on somebody's account. Both must produce the
same email and the same OTP row, or the person following the code would hit
two different validity rules depending on who asked.
"""
from django.utils import timezone

from core.services.email_branding import email_brand
from core.services.email_sender import email_sender
from core.services.send_email_v2 import send_email_async

from .models import PasswordResetRequest
from .utils import OTP_EXPIRY_MINUTES, generate_otp_code, hash_value, otp_expiry


def start_password_reset(user) -> None:
    """
    Invalidate any live code for the account, mint a fresh one, and email it.

    The code itself is never stored - only its hash - and never returned to
    the caller, administrator or not. An administrator who could read the
    code could sign in as the person, which is precisely what the reset flow
    exists to avoid; they get the direct set-password route for the cases
    where that is the intent.
    """
    email = user.email.lower().strip()

    PasswordResetRequest.objects.filter(email=email, validated_at__isnull=True).update(
        expires_at=timezone.now()
    )

    code = generate_otp_code()
    PasswordResetRequest.objects.create(
        email=email,
        code_hash=hash_value(code),
        expires_at=otp_expiry(),
    )

    send_email_async(
        subject="Your password reset code",
        to_email=email,
        template_name="password_reset_otp",
        context={
            # Branded with the account's own school, so the reader recognises
            # who the code is from.
            **email_brand(user),
            "code": code,
            "expiry_minutes": OTP_EXPIRY_MINUTES,
        },
        # Sent through the mailbox of the account's own institute or school
        # (or the platform's) - nobody is signed in for a reset.
        sender=email_sender(user),
    )


def mask_email(email: str) -> str:
    """`j.cruz@dnsc.edu.ph` -> `j.***@dnsc.edu.ph`, for telling an administrator where the code went."""
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"
