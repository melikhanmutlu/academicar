"""End-user lifecycle emails sent from the worker loop (never a web request).

Mirrors the institution contract-renewal reminder pattern (see
``institutions.send_contract_renewal_reminders``) but for individual paid
models: a per-model paid license has an ``access_expires_at`` window, and an
owner who is never reminded simply lets it lapse. We nudge at 30 / 7 / 1 days
before expiry with a one-click renew link.

De-duplication uses an AuditLog stamp keyed on (model, window, expiry) rather
than a new column, so no migration is required and renewing to a later date
(which changes ``access_expires_at``) automatically re-arms every window.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from models import AuditLog, Model3D, Paper, db
from url_helpers import public_url

logger = logging.getLogger(__name__)

# Days-before-expiry buckets. A model crosses each threshold once as it nears
# expiry, producing at most one reminder per bucket per expiry date.
RENEWAL_WINDOWS: tuple[int, ...] = (30, 7, 1)
RENEWAL_EVENT = "model_renewal_reminder_sent"


def _window_for(days_left: int) -> int | None:
    """Smallest bucket the remaining days fall within (1 < 7 < 30)."""
    for window in sorted(RENEWAL_WINDOWS):
        if days_left <= window:
            return window
    return None


def _already_reminded(model_id: str, window: int, expires_iso: str) -> bool:
    rows = AuditLog.query.filter(
        AuditLog.event_type == RENEWAL_EVENT,
        AuditLog.resource_id == str(model_id),
    ).all()
    for row in rows:
        details = row.details or {}
        if details.get("window") == window and details.get("expires_at") == expires_iso:
            return True
    return False


def send_model_renewal_reminders(now: datetime | None = None) -> int:
    """Email owners of paid models whose access window is 30/7/1 days from
    expiry. Idempotent per (model, window, expiry). Returns the number of
    reminders processed (stamped), whether or not mail was delivered. Called
    from the worker loop.
    """
    from payments import PAID_PLAN_KEYS
    from utils.email import send_email

    now = now or datetime.now(UTC)
    now_naive = now.replace(tzinfo=None)
    window_end = now_naive + timedelta(days=max(RENEWAL_WINDOWS))

    candidates = (
        Model3D.query.join(Paper)
        .filter(
            Model3D.license_type.in_(tuple(PAID_PLAN_KEYS)),
            Model3D.access_expires_at.isnot(None),
            Model3D.access_expires_at >= now_naive,
            Model3D.access_expires_at <= window_end,
            Paper.deleted_at.is_(None),
        )
        .all()
    )

    processed = 0
    for model in candidates:
        expires = model.access_expires_at
        days_left = (expires - now_naive).days
        window = _window_for(days_left)
        if window is None:
            continue
        expires_iso = expires.isoformat()
        if _already_reminded(model.id, window, expires_iso):
            continue

        recipient = model.user.email if model.user else None
        db.session.add(
            AuditLog(
                event_type=RENEWAL_EVENT,
                user_id=model.user_id,
                resource_id=str(model.id),
                details={"window": window, "expires_at": expires_iso, "delivered": bool(recipient)},
            )
        )
        processed += 1
        if not recipient:
            continue

        renew_url = public_url("model_upgrade_page", model_id=model.id)
        name = model.display_name or model.original_filename or "your model"
        subject = f"Your AcademicAR model access expires in {max(days_left, 0)} day(s)"
        body = (
            f"The interactive 3D/AR access window for \"{name}\" expires in "
            f"{max(days_left, 0)} day(s).\n\n"
            "Renew now to keep its public link, QR code and AR viewer working "
            "without interruption — the QR code and URL stay the same after "
            f"renewal:\n{renew_url}\n\n"
            "If you let it lapse, the QR and viewer show a graceful "
            "'access unavailable' page instead of a dead link, and you can "
            "renew at any time to restore access."
        )
        try:
            send_email(recipient, subject, body)
        except Exception:
            logger.exception("renewal reminder email failed for model %s", model.id)

    if candidates:
        db.session.commit()
    return processed
