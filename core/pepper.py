"""
Versioned password pepper.

A pepper is a secret that is mixed into every password before it is hashed,
and that lives only in the environment - never in the database beside the
hashes. A stolen database dump is then not enough to start cracking: without
the pepper, no candidate password can even be tested against a hash.

The pepper is versioned so it can be rotated without locking anyone out.
Every hash records the version it was made with (`User.pepper_version`), and
verification uses THAT version; the moment a password verifies under an old
version the account is re-hashed under the current one, which is the only
time the plaintext is ever available to us. Retire an old pepper once no
row still references it - `manage.py pepper_status` counts them.

Version 0 is reserved for hashes that predate the pepper: the raw password
went straight into Django's hasher. They keep verifying and are upgraded on
the user's next sign-in, so turning the pepper on needs no reset campaign.

The mixing is HMAC-SHA256(pepper, password), base64-encoded, and the result
is what Django's normal hasher (PBKDF2 by default) salts and stretches. Two
reasons for the HMAC rather than a bare concatenation: it keeps the pepper
and the password in separate roles, so a chosen password cannot interact
with the secret; and it hands the hasher a fixed 44-character string, so
bcrypt's 72-byte truncation could never silently drop the pepper.
"""

import base64
import hashlib
import hmac

from django.conf import settings

#: Hashes made before the pepper existed - the password went to the hasher as-is.
LEGACY_VERSION = 0


class PepperUnavailable(LookupError):
    """
    The row names a pepper version this deployment no longer has. Nothing
    can be verified against such a hash; the user needs a password reset.
    """

    def __init__(self, version: int):
        super().__init__(f"No PASSWORD_PEPPER_{version} is configured.")
        self.version = version


def current_version() -> int:
    """
    The version new hashes are made with. Settings refuse to boot with no
    pepper configured, so outside a test override this is never 0.
    """
    return settings.PASSWORD_PEPPER_CURRENT_VERSION


def pepper_password(raw_password: str, version: int) -> str:
    """
    The value that goes into Django's hasher for `raw_password` under
    `version`. Deterministic, so the same call verifies and creates.
    """
    if version == LEGACY_VERSION:
        return raw_password
    try:
        secret = settings.PASSWORD_PEPPERS[version]
    except KeyError:
        raise PepperUnavailable(version) from None
    digest = hmac.new(
        secret.encode("utf-8"), raw_password.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("ascii")
