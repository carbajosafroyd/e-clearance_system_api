from datetime import datetime
from config import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
import threading
from django.core.mail import get_connection


def send_email(
    *,
    subject: str,
    to_email: str | list[str],
    template_name: str,
    context: dict,
    from_email: str | None = None,
):
    """
    Generic email sender (HTML + text fallback)
    """

    if isinstance(to_email, str):
        to_email = [to_email]

    from_email = from_email or settings.DEFAULT_FROM_EMAIL

    html_content = render_to_string(f"email_templates/{template_name}.html", context)
    text_content = render_to_string(f"email_templates/{template_name}.txt", context)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email,
        to=to_email,
    )

    email.attach_alternative(html_content, "text/html")
    email.send()
