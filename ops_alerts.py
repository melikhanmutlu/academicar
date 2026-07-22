"""Operational alerts emailed from the worker loop (never a web request).

The admin storage page already *surfaces* durable-storage (R2) mirror failures,
but that is pull-only: someone has to open the page. This module makes the
signal push: when models are left un-mirrored, admins are emailed once per day
so a silent, compounding data-durability problem cannot hide behind an
unvisited dashboard.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from flask import current_app

from models import AuditLog, Model3D, User, db

logger = logging.getLogger(__name__)

R2_ALERT_EVENT = "r2_mirror_alert_sent"


def _admin_recipients() -> list[str]:
    """Configured admin emails (ADMIN_EMAILS) plus every is_admin user."""
    emails: set[str] = set()
    source = current_app.config.get("ADMIN_EMAILS", [])
    if isinstance(source, str):
        source = source.split(",")
    for entry in source:
        cleaned = str(entry).strip().lower()
        if cleaned:
            emails.add(cleaned)
    for user in User.query.filter(User.is_admin.is_(True)).all():
        if user.email:
            emails.add(user.email.strip().lower())
    return sorted(emails)


def _alerted_within(now: datetime, hours: int = 24) -> bool:
    cutoff = (now - timedelta(hours=hours)).replace(tzinfo=None)
    return (
        AuditLog.query.filter(
            AuditLog.event_type == R2_ALERT_EVENT,
            AuditLog.timestamp >= cutoff,
        ).first()
        is not None
    )


def send_r2_mirror_failure_alert(now: datetime | None = None, min_failures: int = 1) -> int:
    """Email admins when models have unresolved R2 mirror failures.

    At most one alert per 24 hours (deduped via an AuditLog stamp). Returns the
    number of failed models that triggered an alert, or 0 when nothing was sent
    (below threshold or already alerted). The stamp is written before mail is
    attempted so a mail-backend outage cannot re-alert every poll cycle.
    """
    from utils.email import send_email

    now = now or datetime.now(UTC)
    failed = Model3D.query.filter(Model3D.r2_mirror_failed_at.isnot(None)).count()
    if failed < max(1, min_failures):
        return 0
    if _alerted_within(now):
        return 0

    recipients = _admin_recipients()
    db.session.add(
        AuditLog(
            event_type=R2_ALERT_EVENT,
            details={"failed": failed, "recipients": len(recipients)},
        )
    )
    db.session.commit()

    if not recipients:
        logger.warning("R2 mirror alert: %d failed model(s) but no admin recipients configured", failed)
        return failed

    subject = f"AcademicAR: {failed} model(s) failed to mirror to durable storage"
    body = (
        f"{failed} model file set(s) could not be mirrored to durable object "
        "storage (R2) and currently exist only on the ephemeral local volume.\n\n"
        "If the local volume is recycled before these are re-mirrored, the files "
        "are lost. Review them under Admin → Storage and re-run the mirror.\n\n"
        "This is an automated once-daily alert from the AcademicAR worker."
    )
    for recipient in recipients:
        try:
            send_email(recipient, subject, body)
        except Exception:
            logger.exception("R2 mirror alert email failed for %s", recipient)
    return failed
