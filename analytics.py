"""Privacy-conscious product analytics helpers for AcademicAR."""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from flask import current_app, g, has_request_context, request
from flask_login import current_user
from sqlalchemy import func

from models import AnalyticsEvent, Model3D, db


ANALYTICS_COOKIE = "aar_vid"
ALLOWED_BROWSER_EVENTS = {"viewer_ar_started", "viewer_fullscreen_opened", "share_link_copied"}


def _hash(value: str) -> str:
    secret = current_app.config.get("SECRET_KEY", "academicar")
    return hashlib.sha256(f"{secret}:{value}".encode("utf-8")).hexdigest()


def visitor_hash() -> str:
    token = request.cookies.get(ANALYTICS_COOKIE)
    if not token or len(token) > 160:
        token = secrets.token_urlsafe(24)
        g.analytics_cookie_value = token
    return _hash(token)


def session_hash() -> str:
    token = request.cookies.get("session") or visitor_hash()
    return _hash(token)


def device_type() -> str:
    ua = (request.user_agent.string or "").lower()
    if any(term in ua for term in ("ipad", "tablet")):
        return "tablet"
    if any(term in ua for term in ("mobile", "iphone", "android")):
        return "mobile"
    return "desktop"


def track_event(
    event_name: str,
    *,
    owner_user_id: int | None = None,
    project_id: int | None = None,
    model_id: str | None = None,
    properties: dict | None = None,
) -> None:
    """Persist an event without retaining direct visitor identifiers."""
    try:
        request_context = has_request_context()
        referrer = urlparse(request.referrer or "").hostname if request_context else None
        country = (request.headers.get("CF-IPCountry") or "").upper() if request_context else ""
        event = AnalyticsEvent(
            event_name=event_name[:64],
            owner_user_id=owner_user_id,
            actor_user_id=current_user.id if request_context and current_user.is_authenticated else None,
            project_id=project_id,
            model_id=model_id,
            visitor_hash=visitor_hash() if request_context else None,
            session_hash=session_hash() if request_context else None,
            country_code=country if len(country) == 2 and country.isalpha() else None,
            device_type=device_type() if request_context else None,
            referrer_domain=referrer[:255] if referrer else None,
            utm_source=((request.args.get("utm_source") or "")[:120] or None) if request_context else None,
            utm_medium=((request.args.get("utm_medium") or "")[:120] or None) if request_context else None,
            utm_campaign=((request.args.get("utm_campaign") or "")[:120] or None) if request_context else None,
            properties=properties or {},
        )
        db.session.add(event)
        db.session.commit()
    except Exception:
        current_app.logger.exception("Could not record analytics event %s", event_name)
        db.session.rollback()


def apply_analytics_cookie(response):
    token = getattr(g, "analytics_cookie_value", None)
    if token:
        response.set_cookie(
            ANALYTICS_COOKIE,
            token,
            max_age=60 * 60 * 24 * 365,
            secure=request.is_secure,
            httponly=True,
            samesite="Lax",
        )
    return response


def analytics_snapshot(owner_user_id: int | None = None, days: int = 30) -> dict:
    """Return a compact, role-safe dashboard payload from first-party events."""
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    query = AnalyticsEvent.query.filter(AnalyticsEvent.occurred_at >= start)
    if owner_user_id is not None:
        query = query.filter(AnalyticsEvent.owner_user_id == owner_user_id)

    def count(name: str) -> int:
        return query.filter(AnalyticsEvent.event_name == name).count()

    views_query = query.filter(AnalyticsEvent.event_name == "model_viewed")
    top_rows = (
        views_query.filter(AnalyticsEvent.model_id.isnot(None))
        .with_entities(AnalyticsEvent.model_id, func.count(AnalyticsEvent.id))
        .group_by(AnalyticsEvent.model_id)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .limit(5)
        .all()
    )
    model_ids = [model_id for model_id, _ in top_rows]
    models = {model.id: model for model in Model3D.query.filter(Model3D.id.in_(model_ids)).all()} if model_ids else {}
    trend = []
    for offset in range(days - 1, -1, -1):
        day_start = (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        day_query = query.filter(AnalyticsEvent.occurred_at >= day_start, AnalyticsEvent.occurred_at < day_end)
        trend.append({"label": day_start.strftime("%b %d"), "views": day_query.filter(AnalyticsEvent.event_name == "model_viewed").count()})

    def breakdown(column):
        return [
            {"label": label, "count": count_value}
            for label, count_value in (
                query.with_entities(column, func.count(AnalyticsEvent.id))
                .filter(column.isnot(None))
                .group_by(column)
                .order_by(func.count(AnalyticsEvent.id).desc())
                .limit(5)
                .all()
            )
        ]

    return {
        "days": days,
        "views": views_query.count(),
        "unique_visitors": views_query.with_entities(AnalyticsEvent.visitor_hash).distinct().count(),
        "qr_scans": count("qr_scanned"),
        "review_visits": count("review_link_opened"),
        "shares": count("share_link_copied"),
        "ar_starts": count("viewer_ar_started"),
        "project_views": count("project_viewed"),
        "projects_created": count("project_created"),
        "models_uploaded": count("model_uploaded"),
        "conversion_completed": count("model_conversion_completed"),
        "conversion_failed": count("model_conversion_failed"),
        "active_creators": query.filter(AnalyticsEvent.event_name.in_(("project_created", "model_uploaded"))).with_entities(AnalyticsEvent.actor_user_id).distinct().count(),
        "top_models": [{"model": models.get(model_id), "count": count_value} for model_id, count_value in top_rows],
        "trend": trend,
        "countries": breakdown(AnalyticsEvent.country_code),
        "devices": breakdown(AnalyticsEvent.device_type),
        "sources": breakdown(AnalyticsEvent.referrer_domain),
    }
