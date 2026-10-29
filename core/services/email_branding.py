"""
Who an outgoing email says it is from.

Every transactional email this API sends is sent *on behalf of a school* —
the school of the staff member who issued the invitation, or the school the
recipient's account belongs to. Nothing here may hardcode one institution:
the templates read `brand_name` and the resolution below decides what that
is, falling back to a neutral product name when the account has no school
attached yet (a superuser with no institute, a password reset for an email
that is not on file).

Resolution order, first hit wins:

    explicit ``school=`` -> user.school -> user.institute.school -> default

The default comes from ``settings.EMAIL_BRAND_NAME`` (env ``EMAIL_BRAND_NAME``)
so a deployment can name itself without touching a template.

The logo follows the narrowest thing the mail belongs to:

    institute.logo -> school.logo -> EMAIL_BRAND_LOGO_URL -> none

so an institute officer's invitations carry the institute's Cloudinary
logo, a school administrator's the school's, and a superuser's (attached to
neither) the platform default from the environment. Which *mailbox* sends
the message is a separate question - core/services/email_sender.py.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


DEFAULT_BRAND_NAME = "Collection Management System"


def _default_brand_name() -> str:
    return getattr(settings, "EMAIL_BRAND_NAME", None) or DEFAULT_BRAND_NAME


def resolve_school(user=None, *, school=None, institute=None):
    """The School an email should be branded as, or None."""
    if school is not None:
        return school

    if institute is None and user is not None:
        institute = getattr(user, "institute", None)

    if user is not None:
        user_school = getattr(user, "school", None)
        if user_school is not None:
            return user_school

    if institute is not None:
        return getattr(institute, "school", None)

    return None


def _logo_url(logo: str | None, version: int | None = None) -> str | None:
    """
    School.logo holds either an absolute URL or a Cloudinary public id.
    Absolute URLs pass straight through; anything else is resolved, and a
    misconfigured Cloudinary must never take an email down with it.
    """
    if not logo:
        return None

    if logo.startswith(("http://", "https://", "//")):
        return logo

    try:
        from api.utils.cloudinary.cloudinary_utils import get_file_url

        return get_file_url(logo, version=version)
    except Exception:
        logger.warning("Could not resolve email logo for %r", logo, exc_info=True)
        return None


def brand_logo_url(*, school=None, institute=None) -> str | None:
    """
    institute logo -> school logo -> EMAIL_BRAND_LOGO_URL -> None.

    Each is a Cloudinary public id (resolved on the account it was uploaded
    to) or a plain URL; the platform default is always a plain URL.
    """
    for owner in (institute, school):
        if owner is None:
            continue
        url = _logo_url(
            getattr(owner, "logo", None),
            version=getattr(owner, "logo_cloudinary_version", None),
        )
        if url:
            return url
    return getattr(settings, "EMAIL_BRAND_LOGO_URL", None) or None


def email_brand(user=None, *, school=None, institute=None) -> dict:
    """
    The branding half of an email context.

    Merge it into whatever the template needs:

        context = {**email_brand(request.user), "code": code}

    Keys: ``brand_name``, ``brand_short_name``, ``brand_logo_url``,
    ``brand_location``, ``brand_color``, ``institute_name``,
    ``support_email``, ``year``.
    """
    if institute is None and user is not None:
        institute = getattr(user, "institute", None)

    resolved = resolve_school(user, school=school, institute=institute)

    brand_name = getattr(resolved, "school_name", None) or _default_brand_name()
    short_name = getattr(resolved, "short_name", None) or None

    return {
        "brand_name": brand_name,
        "brand_short_name": short_name or brand_name,
        "brand_logo_url": brand_logo_url(school=resolved, institute=institute),
        "brand_location": getattr(resolved, "location", None),
        "brand_color": getattr(settings, "EMAIL_BRAND_COLOR", None) or "#04932a",
        "institute_name": getattr(institute, "institute_name", None),
        "support_email": getattr(settings, "SUPPORT_EMAIL", None)
        or settings.DEFAULT_FROM_EMAIL,
        "year": timezone.now().year,
    }


def brand_name_for(user=None, *, school=None, institute=None) -> str:
    """Just the display name — for subject lines."""
    return email_brand(user, school=school, institute=institute)["brand_name"]
