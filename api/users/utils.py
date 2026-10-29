"""Helpers for creating invited accounts."""

import secrets

from django.conf import settings


# No 0/O/1/l/I: these passwords are read off a screen and typed by hand, and
# a character the reader guesses wrong costs a support message.
_PASSWORD_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_PASSWORD_DIGITS = "23456789"
_PASSWORD_ALPHABET = _PASSWORD_LETTERS + _PASSWORD_DIGITS


def generate_temporary_password(length: int | None = None) -> str:
    """
    A one-time password for an invited account.

    Random over a 57-character alphabet, so 12 characters is roughly 70 bits
    — far past anything Django's validators would reject, while still being
    something a student can copy out of an email. Guaranteed to mix letters
    and digits so it can never come out entirely numeric.
    """
    length = length or getattr(settings, "INVITE_TEMPORARY_PASSWORD_LENGTH", 12)
    length = max(10, int(length))

    while True:
        password = "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))
        if any(c in _PASSWORD_DIGITS for c in password) and any(
            c in _PASSWORD_LETTERS for c in password
        ):
            return password


def generate_username(email: str, *, taken: set[str] | None = None) -> str:
    """
    Reserve the username an invited account will get, at invite time.

    Sign-in is by username, so the invitation has to be able to name it —
    which means it cannot wait until activation. Uniqueness is checked
    against existing users, against usernames already reserved by unused
    invitations, and against `taken` (the batch being built right now).
    """
    from api.users.models import User, UserRegistrationToken

    base = (email or "").split("@")[0].strip().lower()
    base = "".join(c for c in base if c.isalnum() or c in {".", "_", "-"}) or "user"
    base = base[:140]

    taken = taken or set()

    candidate = base
    counter = 1
    while (
        candidate in taken
        or User.objects.filter(username__iexact=candidate).exists()
        or UserRegistrationToken.objects.filter(
            username__iexact=candidate, used_at__isnull=True
        ).exists()
    ):
        candidate = f"{base}{counter}"
        counter += 1

    return candidate
