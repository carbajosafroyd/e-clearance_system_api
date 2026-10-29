"""
Whose mailbox an outgoing email goes out *through*.

Branding (core/services/email_branding.py) decides what an email looks
like; this decides which SMTP account actually sends it. The two follow
different things on purpose: branding follows the account the mail is
*about*, the sender follows the account that *caused* it - or, for mail
nobody is signed in for (a password reset), the account it concerns.

The rule, first hit wins:

    superuser                          the platform mailbox from the environment
                                       (EMAIL_HOST_USER / EMAIL_HOST_PASSWORD /
                                       DEFAULT_FROM_EMAIL)
    an institute with a sender set up  that institute's mailbox
    a school with a sender set up      that school's mailbox
    nothing configured                 the platform mailbox

So a school administrator - attached to `user.school`, no institute - sends
as their school; an institute officer sends as their institute, or as the
school when the institute has not set one up; a superuser always sends as
the platform. "Set up" means both an address and a password that still
decrypts. A row whose password cannot be read (the key was rotated away)
logs a warning and is skipped, so a lost key degrades to platform mail
rather than to no mail.

Use it beside the branding:

    send_email_async(
        subject=..., to_email=..., template_name=..., context={**email_brand(user), ...},
        sender=email_sender(user),
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.core import mail

from core.email_secrets import EmailSecretUnreadable

logger = logging.getLogger(__name__)

PLATFORM = "platform"
SCHOOL = "school"
INSTITUTE = "institute"


@dataclass(frozen=True)
class EmailSender:
    """
    One SMTP identity. `username`/`password` are None for the platform
    sender, which means "whatever the settings say" to Django's backend.
    """

    from_email: str
    username: str | None = None
    password: str | None = None
    source: str = PLATFORM
    owner_id: int | None = None
    #: Who this is, for people: the institute's or school's name, or the
    #: platform's brand name. Never anything secret.
    label: str = ""

    @property
    def is_platform(self) -> bool:
        return self.source == PLATFORM

    @property
    def key(self) -> tuple[str, int | None]:
        """Messages with the same key can share one connection."""
        return (self.source, self.owner_id)

    def connection(self, **kwargs):
        """
        An email backend authenticated as this sender. Host, port and TLS
        come from the settings for every sender; only the login differs.
        """
        if self.is_platform:
            return mail.get_connection(**kwargs)
        return mail.get_connection(username=self.username, password=self.password, **kwargs)

    def describe(self) -> str:
        return f"{self.source}:{self.owner_id or '-'} <{self.from_email}>"

    def public(self) -> dict:
        """
        The part the console may show - which mailbox, as whom - so a form
        can say "leave this empty and mail goes out as X". No login details.
        """
        return {"source": self.source, "from_email": self.from_email, "label": self.label}


def platform_configured() -> bool:
    """Does the environment name a mailbox at all? (Local runs often don't.)"""
    return bool(settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD)


def platform_sender() -> EmailSender:
    return EmailSender(
        from_email=settings.DEFAULT_FROM_EMAIL or settings.EMAIL_HOST_USER or "",
        label=getattr(settings, "EMAIL_BRAND_NAME", None) or "Platform",
    )


def _sender_from_row(row, source: str) -> EmailSender | None:
    """The row's own sender, or None when it has none we can use."""
    if row is None or not getattr(row, "email_sender_configured", False):
        return None
    try:
        password = row.get_email_host_password()
    except EmailSecretUnreadable:
        logger.warning(
            "The email sender on %s #%s cannot be decrypted with the current "
            "EMAIL_SECRETS_KEY; falling back to the next sender in the chain.",
            source,
            row.pk,
        )
        return None
    if not password:
        return None
    return EmailSender(
        from_email=row.effective_from_email,
        username=row.email_host_user,
        password=password,
        source=source,
        owner_id=row.pk,
        label=getattr(row, "institute_name", None) or getattr(row, "school_name", None) or "",
    )


def sender_for(*, school=None, institute=None) -> EmailSender:
    """The tenant chain with no user involved: institute -> school -> platform."""
    if institute is not None:
        found = _sender_from_row(institute, INSTITUTE)
        if found:
            return found
        if school is None:
            school = getattr(institute, "school", None)
    if school is not None:
        found = _sender_from_row(school, SCHOOL)
        if found:
            return found
    return platform_sender()


def email_sender(user=None, *, school=None, institute=None) -> EmailSender:
    """
    The sender for mail caused by (or concerning) `user`.

    Explicit `school=` / `institute=` stand in for the user's own when the
    caller knows better; a superuser is always the platform.
    """
    if user is not None and getattr(user, "is_superuser", False):
        return platform_sender()

    if institute is None and user is not None:
        institute = getattr(user, "institute", None)
    if school is None and user is not None:
        school = getattr(user, "school", None)

    return sender_for(school=school, institute=institute)
