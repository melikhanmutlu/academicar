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


IMPACT_REPORT_EVENT = "impact_report_sent"


def impact_unsubscribe_token(user_id: int) -> str:
    from flask import current_app
    from itsdangerous import URLSafeSerializer

    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="impact-report-unsubscribe").dumps({"uid": user_id})


def user_id_from_impact_unsubscribe_token(token: str) -> int | None:
    from flask import current_app
    from itsdangerous import BadSignature, URLSafeSerializer

    try:
        data = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="impact-report-unsubscribe").loads(token)
    except BadSignature:
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    return uid if isinstance(uid, int) else None


def _previous_month(now: datetime) -> tuple[datetime, datetime, str]:
    """(start, end, 'YYYY-MM') of the calendar month before ``now`` (naive UTC)."""
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return first_prev, first_this, first_prev.strftime("%Y-%m")


def send_monthly_impact_reports(now: datetime | None = None) -> int:
    """Email each owner a short summary of last month's reader activity on
    their models (views, unique visitors, QR scans, AR starts, top model).
    Only owners whose models were actually viewed, once per month, never to
    users who opted out or are deactivated. Called from the worker loop."""
    from sqlalchemy import func

    from models import AnalyticsEvent, User
    from utils.email import send_email

    now = now or datetime.now(UTC)
    start, end, month_key = _previous_month(now)
    window = AnalyticsEvent.query.filter(
        AnalyticsEvent.occurred_at >= start,
        AnalyticsEvent.occurred_at < end,
        AnalyticsEvent.owner_user_id.isnot(None),
    )
    owner_ids = [
        uid for (uid,) in window.filter(AnalyticsEvent.event_name == "model_viewed")
        .with_entities(AnalyticsEvent.owner_user_id).distinct().all()
    ]
    sent = 0
    for owner_id in owner_ids:
        user = db.session.get(User, owner_id)
        if user is None or not user.email or user.impact_report_opt_out or user.deactivated_at is not None:
            continue
        stamp = f"{owner_id}:{month_key}"
        if AuditLog.query.filter_by(event_type=IMPACT_REPORT_EVENT, resource_id=stamp).first():
            continue
        mine = window.filter(AnalyticsEvent.owner_user_id == owner_id)
        views = mine.filter(AnalyticsEvent.event_name == "model_viewed")

        def count(name):
            return mine.filter(AnalyticsEvent.event_name == name).count()

        unique = views.with_entities(func.count(func.distinct(AnalyticsEvent.visitor_hash))).scalar() or 0
        top = (
            views.filter(AnalyticsEvent.model_id.isnot(None))
            .with_entities(AnalyticsEvent.model_id, func.count(AnalyticsEvent.id))
            .group_by(AnalyticsEvent.model_id)
            .order_by(func.count(AnalyticsEvent.id).desc())
            .first()
        )
        top_line = ""
        if top is not None:
            top_model = db.session.get(Model3D, top[0])
            if top_model is not None:
                top_name = top_model.display_name or top_model.original_filename or "3D model"
                top_line = f"Most viewed: {top_name} ({top[1]} views)\n"
        month_label = start.strftime("%B %Y")
        body = (
            f"Hi {user.username},\n\n"
            f"Here is how readers engaged with your AcademicAR models in {month_label}:\n\n"
            f"  Views: {views.count()}\n"
            f"  Unique visitors: {int(unique)}\n"
            f"  QR scans: {count('qr_scanned')}\n"
            f"  AR starts: {count('viewer_ar_started')}\n"
            f"{('  ' + top_line) if top_line else ''}\n"
            f"Full details and a CSV for your reports: {public_url('insights', days=30)}\n\n"
            "You get this summary once a month when your models were viewed. "
            f"Stop these emails: {public_url('impact_report_unsubscribe', token=impact_unsubscribe_token(user.id))}\n"
        )
        delivered = False
        try:
            delivered = bool(send_email(user.email, f"Your AcademicAR models in {month_label}", body))
        except Exception:
            logger.exception("impact report email failed for user %s", user.id)
        db.session.add(AuditLog(event_type=IMPACT_REPORT_EVENT, user_id=user.id, resource_id=stamp,
                                details={"month": month_key, "delivered": delivered}))
        sent += 1
    if sent:
        db.session.commit()
    return sent
