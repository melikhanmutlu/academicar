"""Minimal, dependency-free transactional email sender.

Uses the standard library ``smtplib`` driven entirely by ``MAIL_*`` config.
When no SMTP server is configured (typical for local development) the message
is logged instead of sent, so flows that depend on it still complete and the
delivery link can be copied from the logs.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

logger = logging.getLogger(__name__)


def send_email(to_address: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True if actually handed to an SMTP
    server, False if SMTP is unconfigured or sending failed (in which case the
    message is logged so non-production environments can still proceed)."""
    server = current_app.config.get("MAIL_SERVER")
    sender = (
        current_app.config.get("MAIL_DEFAULT_SENDER")
        or current_app.config.get("CONTACT_EMAIL")
        or "no-reply@academicar.com"
    )

    # Strip CR/LF from header values to prevent email header injection: a
    # newline in the recipient or subject could otherwise smuggle additional
    # headers (Bcc, Content-Type, ...) into the outgoing message.
    to_address = (to_address or "").replace("\r", "").replace("\n", "")
    subject = (subject or "").replace("\r", "").replace("\n", "")

    if not server:
        logger.warning(
            "MAIL_SERVER not configured; email NOT sent to %s.\nSubject: %s\n%s",
            to_address,
            subject,
            body,
        )
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_address
    message.set_content(body)

    port = int(current_app.config.get("MAIL_PORT", 587))
    use_tls = bool(current_app.config.get("MAIL_USE_TLS", True))
    username = current_app.config.get("MAIL_USERNAME")
    password = current_app.config.get("MAIL_PASSWORD")

    try:
        with smtplib.SMTP(server, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        logger.info("Sent email to %s (subject=%s)", to_address, subject)
        return True
    except Exception as exc:  # pragma: no cover - network dependent
        logger.error("Failed to send email to %s: %s", to_address, exc)
        return False
