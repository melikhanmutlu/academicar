"""Privacy-conscious product analytics helpers for AcademicAR."""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from flask import current_app, g, has_request_context, request
from flask_login import current_user
from sqlalchemy import func

from licensing import plan_supports_feature
from models import AnalyticsEvent, Model3D, Paper, db

ANALYTICS_COOKIE = "aar_vid"
ALLOWED_BROWSER_EVENTS = {"viewer_ar_started", "viewer_fullscreen_opened", "share_link_copied"}


def _hash(value: str) -> str:
    secret = current_app.config.get("SECRET_KEY", "academicar")
    return hashlib.sha256(f"{secret}:{value}".encode()).hexdigest()


def visitor_hash() -> str:
    token = request.cookies.get(ANALYTICS_COOKIE) or getattr(g, "analytics_cookie_value", None)
    if not token or len(token) > 160:
        token = secrets.token_urlsafe(24)
        g.analytics_cookie_value = token
    return _hash(token)


def browser_event_is_duplicate(
    event_name: str,
    model_id: str,
    *,
    within_seconds: int = 15,
) -> bool:
    """Suppress rapid repeats from the same pseudonymous visitor/model.

    This is not the only abuse control (the route is also rate-limited), but it
    prevents double-clicks, repeated lifecycle callbacks, and simple event
    replay from inflating owner-facing engagement metrics.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=max(1, within_seconds))
    return (
        AnalyticsEvent.query.filter(
            AnalyticsEvent.event_name == event_name,
            AnalyticsEvent.model_id == model_id,
            AnalyticsEvent.visitor_hash == visitor_hash(),
            AnalyticsEvent.occurred_at >= cutoff,
        ).first()
        is not None
    )


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


# Ordered acquisition/activation/revenue funnel. Each stage is an event the
# product emits at a real transition, so the drop between two stages points at a
# concrete step to fix rather than a vanity aggregate.
FUNNEL_STAGES: tuple[tuple[str, str], ...] = (
    ("user_registered", "Signed up"),
    ("project_created", "Project created"),
    ("model_uploaded", "Model uploaded"),
    ("model_conversion_completed", "Conversion done"),
    ("checkout_started", "Checkout started"),
    ("payment_succeeded", "Paid"),
)


def funnel_snapshot(days: int = 30) -> dict:
    """Distinct-user conversion funnel across the acquisition→revenue journey.

    A stage counts distinct users (the acting user, or the owning user when the
    event is fired server-side without an actor, e.g. a payment webhook), so the
    numbers read as "how many people reached this step", not raw event volume.
    """
    now = datetime.now(UTC)
    start = now - timedelta(days=days)
    base = AnalyticsEvent.query.filter(AnalyticsEvent.occurred_at >= start)
    user_expr = func.coalesce(AnalyticsEvent.actor_user_id, AnalyticsEvent.owner_user_id)

    stages = []
    top_count: int | None = None
    prev_count: int | None = None
    for event_name, label in FUNNEL_STAGES:
        count = int(
            base.filter(AnalyticsEvent.event_name == event_name)
            .with_entities(func.count(func.distinct(user_expr)))
            .scalar()
            or 0
        )
        if top_count is None:
            top_count = count
        stages.append({
            "event": event_name,
            "label": label,
            "count": count,
            "pct_of_top": round((count / top_count) * 100, 1) if top_count else 0.0,
            "pct_of_prev": (
                100.0 if prev_count is None
                else (round((count / prev_count) * 100, 1) if prev_count else None)
            ),
        })
        prev_count = count
    return {"days": days, "stages": stages}


def analytics_snapshot(owner_user_id: int | None = None, days: int = 30) -> dict:  # noqa: C901
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

    model_metrics = []
    if owner_user_id is not None:
        owned_models = (
            Model3D.query.join(Paper)
            .filter(Model3D.user_id == owner_user_id, Paper.deleted_at.is_(None))
            .order_by(Model3D.created_at.desc())
            .all()
        )
        owned_ids = [model.id for model in owned_models]
        aggregate = {}
        if owned_ids:
            rows = (
                query.with_entities(
                    AnalyticsEvent.model_id,
                    AnalyticsEvent.event_name,
                    func.count(AnalyticsEvent.id),
                    func.count(func.distinct(AnalyticsEvent.visitor_hash)),
                    func.max(AnalyticsEvent.occurred_at),
                )
                .filter(AnalyticsEvent.model_id.in_(owned_ids))
                .group_by(AnalyticsEvent.model_id, AnalyticsEvent.event_name)
                .all()
            )
            for model_id, event_name, total, unique_count, last_at in rows:
                aggregate.setdefault(model_id, {})[event_name] = {
                    "count": int(total or 0), "unique": int(unique_count or 0), "last_at": last_at,
                }

        dimension_maps = {}
        for dimension_name, column in (
            ("country", AnalyticsEvent.country_code),
            ("device", AnalyticsEvent.device_type),
            ("source", AnalyticsEvent.referrer_domain),
        ):
            per_model = {}
            if owned_ids:
                dimension_rows = (
                    query.with_entities(AnalyticsEvent.model_id, column, func.count(AnalyticsEvent.id))
                    .filter(AnalyticsEvent.model_id.in_(owned_ids), column.isnot(None))
                    .group_by(AnalyticsEvent.model_id, column)
                    .order_by(func.count(AnalyticsEvent.id).desc())
                    .all()
                )
                for model_id, label, count_value in dimension_rows:
                    per_model.setdefault(model_id, {"label": label, "count": int(count_value)})
            dimension_maps[dimension_name] = per_model

        engaged_visitors: dict[str, set[str]] = {}
        if owned_ids:
            engagement_rows = (
                query.with_entities(AnalyticsEvent.model_id, AnalyticsEvent.visitor_hash)
                .filter(
                    AnalyticsEvent.model_id.in_(owned_ids),
                    AnalyticsEvent.event_name.in_(("viewer_ar_started", "share_link_copied")),
                    AnalyticsEvent.visitor_hash.isnot(None),
                )
                .distinct()
                .all()
            )
            for model_id, visitor in engagement_rows:
                engaged_visitors.setdefault(model_id, set()).add(visitor)

        for model in owned_models:
            events = aggregate.get(model.id, {})
            views = events.get("model_viewed", {}).get("count", 0)
            unique_viewers = events.get("model_viewed", {}).get("unique", 0)
            ar_starts = events.get("viewer_ar_started", {}).get("count", 0)
            shares = events.get("share_link_copied", {}).get("count", 0)
            engaged_unique = len(engaged_visitors.get(model.id, set()))
            model_metrics.append({
                "model": model,
                "detailed": plan_supports_feature(model.license_type, "detailed_insights"),
                "views": views,
                "unique_visitors": unique_viewers,
                "qr_scans": events.get("qr_scanned", {}).get("count", 0),
                "ar_starts": ar_starts,
                "shares": shares,
                "fullscreen_opens": events.get("viewer_fullscreen_opened", {}).get("count", 0),
                "engaged_visitors": engaged_unique,
                "engagement_rate": round(min((engaged_unique / unique_viewers) * 100, 100), 1) if unique_viewers else 0,
                "last_viewed_at": events.get("model_viewed", {}).get("last_at"),
                "top_country": dimension_maps["country"].get(model.id),
                "top_device": dimension_maps["device"].get(model.id),
                "top_source": dimension_maps["source"].get(model.id),
            })

    return {
        "days": days,
        "views": views_query.count(),
        "unique_visitors": int(
            views_query.with_entities(func.count(func.distinct(AnalyticsEvent.visitor_hash))).scalar()
            or 0
        ),
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
        "model_metrics": model_metrics,
    }
