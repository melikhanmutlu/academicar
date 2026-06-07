"""Minimal, dependency-free transactional email sender.

Two delivery backends, selected by config:

1. **HTTP API (preferred on PaaS):** if ``MAIL_API_KEY`` is set, the message is
   sent via Brevo's transactional HTTP API over port 443. This is the reliable
   path on hosts like Railway that block outbound SMTP ports (587/2525/465).
2. **SMTP (fallback):** if no API key but ``MAIL_SERVER`` is set, the stdlib
   ``smtplib`` path is used.

When neither is configured (typical for local development) the message is
logged instead of sent, so flows that depend on it still complete and the
delivery link can be copied from the logs.
"""
from __future__ import annotations

import json
import logging
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import parseaddr

from flask import current_app

logger = logging.getLogger(__name__)


def _resolve_sender() -> str:
    return (
        current_app.config.get("MAIL_DEFAULT_SENDER")
        or current_app.config.get("CONTACT_EMAIL")
        or "no-reply@academicar.com"
    )


def _send_via_api(api_key: str, sender: str, to_address: str, subject: str, body: str) -> bool:
    """Send through Brevo's transactional HTTP API (port 443, never blocked).

    ``sender`` may be a bare address or a "Name <addr>" form; we split it so the
    API receives a structured sender object.
    """
    api_url = current_app.config.get("MAIL_API_URL") or "https://api.brevo.com/v3/smtp/email"
    sender_name, sender_email = parseaddr(sender)
    sender_obj = {"email": sender_email or sender}
    if sender_name:
        sender_obj["name"] = sender_name

    payload = json.dumps(
        {
            "sender": sender_obj,
            "to": [{"email": to_address}],
            "subject": subject,
            "textContent": body,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        api_url,
        data=payload,
        method="POST",
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            resp.read()
        logger.info("Sent email to %s via API (subject=%s)", to_address, subject)
        return True
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        logger.error("Email API rejected message to %s (HTTP %s): %s", to_address, exc.code, detail)
        return False
    except Exception as exc:  # pragma: no cover - network dependent
        logger.error("Email API call failed for %s: %s", to_address, exc)
        return False


def _send_via_smtp(server: str, sender: str, to_address: str, subject: str, body: str) -> bool:
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
        logger.info("Sent email to %s via SMTP (subject=%s)", to_address, subject)
        return True
    except Exception as exc:  # pragma: no cover - network dependent
        logger.error("Failed to send email to %s: %s", to_address, exc)
        return False


def send_email(to_address: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True if handed to a delivery backend,
    False if email is unconfigured or sending failed (in which case the message
    is logged so non-production environments can still proceed)."""
    sender = _resolve_sender()
    api_key = current_app.config.get("MAIL_API_KEY")
    if api_key:
        return _send_via_api(api_key, sender, to_address, subject, body)

    server = current_app.config.get("MAIL_SERVER")
    if server:
        return _send_via_smtp(server, sender, to_address, subject, body)

    logger.warning(
        "No email backend configured (MAIL_API_KEY/MAIL_SERVER); email NOT sent "
        "to %s.\nSubject: %s\n%s",
        to_address,
        subject,
        body,
    )
    return False
