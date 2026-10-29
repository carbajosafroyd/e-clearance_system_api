import logging
import threading
from collections import defaultdict

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone

from core.services.email_branding import email_brand
from core.services.email_sender import EmailSender, platform_sender

logger = logging.getLogger(__name__)


def _execute_send(email_obj):
    """Internal helper to actually trigger the send"""
    try:
        email_obj.send()
    except Exception as e:
        # Since this is in a thread, we must log errors manually
        logger.error("Email failed: %s", e, exc_info=True)


def _with_brand_defaults(context: dict) -> dict:
    """
    Never render a template with a blank brand.

    Callers pass ``**email_brand(user)`` when they know whose school the mail
    belongs to; this fills the neutral fallback for the ones that don't, so a
    header or footer can't come out empty.
    """
    filled = dict(context)
    filled.setdefault("year", timezone.now().year)
    for key, value in email_brand().items():
        filled.setdefault(key, value)
    return filled


def build_email(
    *,
    subject: str,
    to_email: str | list[str],
    template_name: str,
    context: dict,
    from_email: str | None = None,
    sender: EmailSender | None = None,
) -> EmailMultiAlternatives:
    """
    Render one message. Shared by the single and bulk senders.

    `sender` (core/services/email_sender.py) is the mailbox it goes out
    through - a school's or institute's own Gmail account, or the platform's
    from the environment when none is given. The message carries a
    connection authenticated as that sender, so `.send()` needs nothing
    else; `from_email` overrides only the visible From line.
    """
    if isinstance(to_email, str):
        to_email = [to_email]

    sender = sender or platform_sender()
    full_context = _with_brand_defaults(context)

    html_content = render_to_string(
        f"email_templates/{template_name}.html", full_context
    )
    text_content = render_to_string(
        f"email_templates/{template_name}.txt", full_context
    )

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email or sender.from_email or settings.DEFAULT_FROM_EMAIL,
        to=to_email,
        connection=sender.connection(),
    )
    message.attach_alternative(html_content, "text/html")
    # Remembered so the bulk sender can share one connection per mailbox.
    message.sender = sender
    return message


def send_email_async(
    *,
    subject: str,
    to_email: str | list[str],
    template_name: str,
    context: dict,
    from_email: str | None = None,
    sender: EmailSender | None = None,
):
    email = build_email(
        subject=subject,
        to_email=to_email,
        template_name=template_name,
        context=context,
        from_email=from_email,
        sender=sender,
    )

    # Off the request thread by default: SMTP is slow and nothing the caller
    # is waiting for depends on it. Tests (and anywhere the send has to have
    # happened before the next line) flip EMAIL_SEND_ASYNC off.
    if not getattr(settings, "EMAIL_SEND_ASYNC", True):
        _execute_send(email)
        return

    thread = threading.Thread(target=_execute_send, args=(email,))
    thread.start()


def send_bulk_emails_async(messages: list[dict]):
    """
    Send many invitations, one SMTP connection per mailbox, in the background.

    Each entry is ``{subject, to_email, template_name, context}`` plus an
    optional ``sender`` — the same shape :func:`send_email_async` takes.
    Building the context is the caller's job: only it knows whose school
    issued the invite and which one-time password went to which address.

    A batch normally comes from one account and so goes out through one
    mailbox; if it does not, each mailbox gets its own connection rather
    than the first one's credentials being reused for the rest.
    """
    email_messages = []

    for item in messages:
        try:
            email_messages.append(
                build_email(
                    subject=item["subject"],
                    to_email=item["to_email"],
                    template_name=item["template_name"],
                    context=item["context"],
                    from_email=item.get("from_email"),
                    sender=item.get("sender"),
                )
            )
        except Exception:
            logger.exception("Could not render invitation for %s", item.get("to_email"))

    if not email_messages:
        return

    by_sender: dict[tuple, list] = defaultdict(list)
    for message in email_messages:
        by_sender[message.sender.key].append(message)

    for group in by_sender.values():
        sender = group[0].sender
        try:
            # One connection per mailbox for efficiency; the messages already
            # carry an equivalent one, but send_messages() on a shared
            # connection logs in once instead of once per message.
            sender.connection().send_messages(group)
        except Exception as e:
            logger.error(
                "Background Email Error (%s): %s", sender.describe(), e, exc_info=True
            )
