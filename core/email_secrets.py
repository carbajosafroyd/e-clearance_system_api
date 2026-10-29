"""
Encryption for the per-school / per-institute SMTP app passwords.

Why encrypted and not hashed
----------------------------
A password hash is one-way: it lets us check a password somebody typed, and
nothing else. That is exactly right for account passwords and exactly wrong
here, because the server has to hand the *real* app password to Gmail every
time it opens an SMTP connection on a school's behalf. So the value is
encrypted at rest instead, with a key that lives only in the environment:
a database dump alone yields ciphertext, and the plaintext is never sent
back out of the API (the serializers expose `has_email_password`, a bool).

The key
-------
`EMAIL_SECRETS_KEY` - a Fernet key (32 url-safe base64 bytes). Generate one:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

It is deliberately its own secret, not DJANGO_SECRET_KEY: that one signs
sessions and tokens, and rotating either must not touch the other. Locally,
with nothing set, a key is derived from DJANGO_SECRET_KEY so development
needs no extra configuration; production.py refuses to boot that way when
IS_DEPLOYED=True.

Rotation: set `EMAIL_SECRETS_KEY=<new>,<old>`. The first key encrypts, every
key decrypts (MultiFernet), so existing rows keep working; re-save a row and
it moves to the new key. Drop the old key once nothing needs it - a row
encrypted under a key that is gone falls back to the next sender in the
chain (see core/services/email_sender.py) and logs a warning, it does not
take the email down with it.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class EmailSecretUnreadable(LookupError):
    """The ciphertext was made with a key this deployment no longer has."""


def _derived_key() -> bytes:
    """
    The local-development fallback: a key made from DJANGO_SECRET_KEY, so a
    fresh checkout can save an app password without generating anything.
    """
    secret = getattr(settings, "SECRET_KEY", None)
    if not secret:
        raise ImproperlyConfigured(
            "EMAIL_SECRETS_KEY is not set and there is no DJANGO_SECRET_KEY to "
            "derive one from. Generate a key with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and set EMAIL_SECRETS_KEY."
        )
    digest = hashlib.sha256(f"email-secrets:{secret}".encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> MultiFernet:
    keys = [k for k in getattr(settings, "EMAIL_SECRETS_KEYS", ()) if k]
    if not keys:
        return MultiFernet([Fernet(_derived_key())])
    try:
        return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "EMAIL_SECRETS_KEY is not a valid Fernet key. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`."
        ) from exc


def encrypt_secret(plaintext: str | None) -> str:
    """Ciphertext for storage; an empty input stores as empty, not as an encrypted ''."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str | None) -> str:
    """
    The plaintext, or '' for an empty column.

    Raises :class:`EmailSecretUnreadable` when no configured key can open
    it - the key was rotated away, or the row was copied from another
    environment. Callers that send mail should treat that as "not
    configured" and fall through, never as a reason to fail the request.
    """
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise EmailSecretUnreadable(
            "The stored app password cannot be decrypted with the configured "
            "EMAIL_SECRETS_KEY(s)."
        ) from exc
