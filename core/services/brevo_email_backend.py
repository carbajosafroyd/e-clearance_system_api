"""
Brevo (formerly Sendinblue) HTTP REST API Email Backend.

Sends transactional emails via HTTPS (Port 443) using Brevo's v3 API.
This completely avoids cloud hosting firewall blocks on outbound SMTP ports (25, 465, 587)
such as those enforced on Render's Free tier.
"""

from __future__ import annotations

import logging
import os
import requests
from email.utils import parseaddr
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class BrevoEmailBackend(BaseEmailBackend):
    """
    A Django email backend that sends messages through Brevo's HTTPS REST API.
    """

    def __init__(self, api_key: str | None = None, fail_silently: bool = False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        raw_key = api_key or getattr(settings, "BREVO_API_KEY", None) or os.getenv("BREVO_API_KEY", "")
        self.api_key = str(raw_key).strip().strip("'\"")

    def send_messages(self, email_messages: list[EmailMessage]) -> int:
        if not email_messages:
            return 0

        if not self.api_key:
            msg = "BrevoEmailBackend cannot send: BREVO_API_KEY is not configured."
            logger.error(msg)
            if self.fail_silently:
                return 0
            raise ValueError(msg)

        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        timeout = getattr(settings, "EMAIL_TIMEOUT", 15)
        num_sent = 0

        for message in email_messages:
            try:
                # Extract HTML alternative if present
                html_content = None
                if hasattr(message, "alternatives"):
                    for content, mimetype in message.alternatives:
                        if mimetype == "text/html":
                            html_content = content
                            break

                parsed_name, parsed_email = parseaddr(message.from_email or "")
                sender_name = parsed_name or getattr(settings, "EMAIL_BRAND_NAME", "E-Clearance System")
                sender_email = (
                    parsed_email
                    or getattr(settings, "DEFAULT_FROM_EMAIL", None)
                    or getattr(settings, "EMAIL_HOST_USER", None)
                    or "froydcarbajosa14@gmail.com"
                )

                # Format recipients cleanly using parseaddr
                to_list = []
                for addr in message.to:
                    to_name, to_email = parseaddr(addr)
                    recipient_entry = {"email": to_email or addr}
                    if to_name:
                        recipient_entry["name"] = to_name
                    to_list.append(recipient_entry)

                if not to_list:
                    continue

                payload = {
                    "sender": {
                        "name": sender_name,
                        "email": sender_email,
                    },
                    "to": to_list,
                    "subject": message.subject,
                    "textContent": message.body,
                }

                if html_content:
                    payload["htmlContent"] = html_content

                response = requests.post(
                    BREVO_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )

                if response.status_code in (200, 201, 202):
                    num_sent += 1
                    resp_data = response.json()
                    logger.info(
                        "Brevo delivered email to %s: messageId=%s",
                        message.to,
                        resp_data.get("messageId"),
                    )
                else:
                    logger.error(
                        "Brevo API error (HTTP %s): %s",
                        response.status_code,
                        response.text,
                    )
                    if not self.fail_silently:
                        response.raise_for_status()

            except Exception as exc:
                logger.error("Failed to send email via Brevo: %s", exc, exc_info=True)
                if not self.fail_silently:
                    raise exc

        return num_sent
