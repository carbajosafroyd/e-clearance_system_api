"""Generating coupon codes, and the small helpers around them."""

from __future__ import annotations

import secrets

from django.conf import settings


#: No 0/O/1/l/I. These are read off paper, under fluorescent light, and typed
#: by somebody who did not print them - the same reason
#: `api/users/utils.py::generate_temporary_password` uses this alphabet.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: Characters per group, and groups per code: "K7M4-QP2X-9TRB".
GROUP = 4
GROUPS = 3


def code_length() -> int:
    return GROUP * GROUPS


def generate_code(prefix: str = "") -> str:
    """
    A code with roughly 60 bits of entropy, grouped for reading aloud.

    Long enough that guessing is pointless even with a generous throttle:
    32^12 is about 10^18. The grouping is not decoration - an ungrouped
    twelve-character string gets transcribed wrong, and a wrong code at a
    counter is a queue.
    """
    body = "".join(secrets.choice(ALPHABET) for _ in range(code_length()))
    grouped = "-".join(body[i : i + GROUP] for i in range(0, len(body), GROUP))
    prefix = (prefix or "").strip().upper()
    return f"{prefix}-{grouped}" if prefix else grouped


def normalise_code(raw: str) -> str:
    """
    What the student typed, in the form the database stores.

    Case and hyphens are noise: somebody reading a coupon aloud will not
    reproduce them, and refusing a valid coupon over a lowercase letter is
    the sort of thing that sends people to the desk.
    """
    if not raw:
        return ""
    cleaned = "".join(ch for ch in raw.strip().upper() if ch.isalnum() or ch == "-")
    return cleaned


def claim_link(code: str) -> str:
    """
    Where a scanned QR sends someone.

    A frontend URL, not an API one: an unauthenticated scan has to land on
    the sign-in page and come back, which `Login.vue` does with `?redirect=`
    for the password form and with the OAuth `state` parameter for Google -
    Google returns to the bare registered `redirect_uri`, so a query string
    does not survive the round trip.
    """
    base = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    return f"{base}/coupons/claim/{code}"
