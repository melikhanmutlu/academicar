"""Privacy-conscious product analytics helpers for AcademicAR."""
from __future__ import annotations

import hashlib
import secrets
from bisect import bisect_right
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse

from flask import current_app, g, has_request_context, request
from flask_login import current_user
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from licensing import plan_supports_feature
from models import AnalyticsEvent, Model3D, Paper, db

ANALYTICS_COOKIE = "aar_vid"
ALLOWED_BROWSER_EVENTS = {
    "viewer_ar_started", "viewer_fullscreen_opened", "viewer_model_rotated", "share_link_copied",
}
# A visitor "engaged" with a model when they did more than load the page.
ENGAGEMENT_EVENTS = ("viewer_ar_started", "share_link_copied", "viewer_fullscreen_opened", "viewer_model_rotated")
# Rotation and fullscreen were first recorded on this date; before it only AR
# starts and link copies counted as engagement.
ENGAGEMENT_TRACKED_SINCE = date(2026, 10, 5)
DIRECT_SOURCE_LABEL = "Direct / QR"
INTERNAL_SOURCE_LABEL = "AcademicAR pages"
_geoip_reader = None


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
        country = _request_country() if request_context else ""
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


def _request_country() -> str:
    """Two-letter country from Cloudflare's header, else an optional GeoLite2 lookup.

    ``GEOIP_DB_PATH`` points at a MaxMind GeoLite2-Country ``.mmdb`` file; the IP
    is only used for this lookup and is never stored.
    """
    country = (request.headers.get("CF-IPCountry") or "").upper()
    if country:
        return country
    path = current_app.config.get("GEOIP_DB_PATH")
    if not path or not request.remote_addr:
        return ""
    global _geoip_reader
    try:
        if _geoip_reader is None:
            import geoip2.database

            _geoip_reader = geoip2.database.Reader(path)
        return (_geoip_reader.country(request.remote_addr).country.iso_code or "").upper()
    except Exception:
        return ""


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
    start = _windows(days)[1]
    user_expr = func.coalesce(AnalyticsEvent.actor_user_id, AnalyticsEvent.owner_user_id)
    counts = {
        name: int(total or 0)
        for name, total in AnalyticsEvent.query.filter(
            AnalyticsEvent.occurred_at >= start,
            AnalyticsEvent.event_name.in_([name for name, _ in FUNNEL_STAGES]),
        ).with_entities(AnalyticsEvent.event_name, func.count(func.distinct(user_expr))).group_by(AnalyticsEvent.event_name)
    }

    stages = []
    top_count: int | None = None
    prev_count: int | None = None
    for event_name, label in FUNNEL_STAGES:
        count = counts.get(event_name, 0)
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


def _own_activity_excluded(query, owner_user_id: int):
    """Drop the owner's own activity, including logged-out visits from a browser
    the owner has used while signed in (same first-party visitor cookie)."""
    own = aliased(AnalyticsEvent)
    own_visitors = (
        select(own.visitor_hash)
        .where(own.actor_user_id == owner_user_id, own.visitor_hash.isnot(None))
        .distinct()
    )
    return query.filter(
        or_(AnalyticsEvent.actor_user_id.is_(None), AnalyticsEvent.actor_user_id != owner_user_id),
        or_(AnalyticsEvent.visitor_hash.is_(None), AnalyticsEvent.visitor_hash.notin_(own_visitors)),
    )


def _windows(days: int, now: datetime | None = None):
    """Current window = the last ``days`` calendar days (UTC, today included);
    the previous window is the same length immediately before it."""
    now = now or datetime.now(UTC)
    first_day = now.date() - timedelta(days=days - 1)
    start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=UTC)
    return first_day, start, start - timedelta(days=days)


def _totals(query, with_engagement: bool = True) -> dict:
    by_event = {
        name: (int(total or 0), int(unique or 0))
        for name, total, unique in query.with_entities(
            AnalyticsEvent.event_name,
            func.count(AnalyticsEvent.id),
            func.count(func.distinct(AnalyticsEvent.visitor_hash)),
        ).group_by(AnalyticsEvent.event_name)
    }
    engaged = int(
        query.filter(AnalyticsEvent.event_name.in_(ENGAGEMENT_EVENTS))
        .with_entities(func.count(func.distinct(AnalyticsEvent.visitor_hash)))
        .scalar()
        or 0
    ) if with_engagement else 0
    unique_visitors = by_event.get("model_viewed", (0, 0))[1]
    return {
        "views": by_event.get("model_viewed", (0, 0))[0],
        "unique_visitors": unique_visitors,
        "qr_scans": by_event.get("qr_scanned", (0, 0))[0],
        "ar_starts": by_event.get("viewer_ar_started", (0, 0))[0],
        "ar_visitors": by_event.get("viewer_ar_started", (0, 0))[1],
        "shares": by_event.get("share_link_copied", (0, 0))[0],
        "fullscreen_opens": by_event.get("viewer_fullscreen_opened", (0, 0))[0],
        "rotations": by_event.get("viewer_model_rotated", (0, 0))[0],
        "review_visits": by_event.get("review_link_opened", (0, 0))[0],
        "project_views": by_event.get("project_viewed", (0, 0))[0],
        "projects_created": by_event.get("project_created", (0, 0))[0],
        "models_uploaded": by_event.get("model_uploaded", (0, 0))[0],
        "conversion_completed": by_event.get("model_conversion_completed", (0, 0))[0],
        "conversion_failed": by_event.get("model_conversion_failed", (0, 0))[0],
        "engaged_visitors": engaged,
        "engagement_rate": _rate(engaged, unique_visitors),
    }


def _rate(part: int, whole: int) -> float | None:
    """Percentage, or None when there is nothing to divide by (shown as "—")."""
    return round(min(part / whole * 100, 100), 1) if whole else None


def _change(current: int, previous: int) -> dict:
    """Period-over-period change; ``pct`` is None when the previous period was 0."""
    pct = round((current - previous) / previous * 100) if previous else None
    direction = "up" if current > previous else "down" if current < previous else "flat"
    return {"previous": previous, "pct": pct, "direction": direction}


def _trend(query, first_day: date, days: int) -> tuple[str, list[dict]]:
    """Views, QR scans and AR starts bucketed by day (≤30 days), week (90) or
    month (12 months), from a single grouped query."""
    granularity = "day" if days <= 31 else "week" if days <= 120 else "month"
    today = first_day + timedelta(days=days - 1)
    if granularity == "day":
        starts = [first_day + timedelta(days=i) for i in range(days)]
    elif granularity == "week":
        starts = [first_day + timedelta(days=i) for i in range(0, days, 7)]
    else:
        starts, cursor = [], first_day.replace(day=1)
        while cursor <= today:
            starts.append(cursor)
            cursor = (cursor + timedelta(days=32)).replace(day=1)
    fmt = {"day": "%b %d", "week": "%b %d", "month": "%b %Y"}[granularity]
    points = [
        {"label": start.strftime(fmt), "start": start.isoformat(), "views": 0, "qr_scans": 0, "ar_starts": 0}
        for start in starts
    ]
    field = {"model_viewed": "views", "qr_scanned": "qr_scans", "viewer_ar_started": "ar_starts"}
    rows = (
        query.filter(AnalyticsEvent.event_name.in_(tuple(field)))
        .with_entities(func.date(AnalyticsEvent.occurred_at), AnalyticsEvent.event_name, func.count(AnalyticsEvent.id))
        .group_by(func.date(AnalyticsEvent.occurred_at), AnalyticsEvent.event_name)
        .all()
    )
    for day_value, event_name, total in rows:
        day = date.fromisoformat(str(day_value)[:10])
        index = bisect_right(starts, day) - 1
        if 0 <= index < len(points):
            points[index][field[event_name]] += int(total or 0)
    return granularity, points


def _internal_hosts() -> set[str]:
    hosts = {"localhost", "127.0.0.1"}
    site_host = urlparse(current_app.config.get("SITE_URL") or "").hostname
    if site_host:
        bare = site_host.removeprefix("www.")
        hosts |= {bare, f"www.{bare}"}
    if has_request_context() and request.host:
        hosts.add(request.host.split(":")[0])
    return hosts


def _source_label(domain: str | None, internal: set[str]) -> str:
    if not domain:
        return DIRECT_SOURCE_LABEL
    domain = domain.lower()
    if domain in internal or domain.endswith(".up.railway.app"):
        return INTERNAL_SOURCE_LABEL
    return domain.removeprefix("www.")


def _with_share(rows: list[tuple[str, int]], total: int, limit: int) -> list[dict]:
    rows = sorted(rows, key=lambda row: (-row[1], row[0]))[:limit]
    return [{"label": label, "count": count, "pct": _rate(count, total) or 0.0} for label, count in rows]


def _breakdowns(views_query, total_views: int, limit: int = 5) -> dict:
    """Viewer audience only: counting every event mixed sign-ups and uploads
    into "devices" / "countries"."""
    def grouped(column):
        return [
            (label, int(count))
            for label, count in views_query.with_entities(column, func.count(AnalyticsEvent.id)).group_by(column)
        ]

    internal = _internal_hosts()
    sources: dict[str, int] = {}
    for domain, count in grouped(AnalyticsEvent.referrer_domain):
        label = _source_label(domain, internal)
        sources[label] = sources.get(label, 0) + count
    countries = [(label, count) for label, count in grouped(AnalyticsEvent.country_code) if label]
    devices = [(label, count) for label, count in grouped(AnalyticsEvent.device_type) if label]
    return {
        "countries": _with_share(countries, total_views, limit),
        "has_country_data": bool(countries),
        "devices": _with_share(devices, total_views, limit),
        "sources": _with_share(list(sources.items()), total_views, limit + 1),
    }


def _period_summary(base_query, days: int, include_trend: bool = True) -> dict:
    """Totals, change vs the previous period, trend and audience for one
    pre-filtered event query (a whole account, a project or a single model)."""
    first_day, start, previous_start = _windows(days)
    query = base_query.filter(AnalyticsEvent.occurred_at >= start)
    totals = _totals(query, with_engagement=include_trend)
    if not include_trend:
        views_query = query.filter(AnalyticsEvent.event_name == "model_viewed")
        return {
            "days": days,
            **totals,
            "changes": {},
            "trend": [],
            "trend_peak": None,
            **_breakdowns(views_query, totals["views"]),
            "_query": query,
        }
    previous_query = base_query.filter(
        AnalyticsEvent.occurred_at >= previous_start, AnalyticsEvent.occurred_at < start
    )
    previous = _totals(previous_query)
    granularity, trend = _trend(query, first_day, days)
    _, previous_trend = _trend(previous_query, first_day - timedelta(days=days), days)
    for point, before in zip(trend, previous_trend):
        point["previous_views"] = before["views"]
    for point in trend[len(previous_trend):]:
        point["previous_views"] = 0
    views_query = query.filter(AnalyticsEvent.event_name == "model_viewed")
    peak = max(trend, key=lambda point: point["views"]) if trend else None
    changes = {
        key: _change(totals[key], previous[key])
        for key in ("views", "unique_visitors", "qr_scans", "ar_starts", "engaged_visitors", "shares", "review_visits")
    }
    # The previous window predates full engagement tracking: not like for like.
    if previous_start.date() < ENGAGEMENT_TRACKED_SINCE:
        changes["engaged_visitors"] = None
    return {
        "days": days,
        **totals,
        "changes": changes,
        "engagement_since": ENGAGEMENT_TRACKED_SINCE,
        "engagement_partial": first_day < ENGAGEMENT_TRACKED_SINCE,
        "trend": trend,
        "trend_granularity": granularity,
        "trend_peak": peak if peak and peak["views"] else None,
        "trend_max": max(
            [max(point["views"], point["previous_views"], point["qr_scans"]) for point in trend] or [0]
        ),
        **_breakdowns(views_query, totals["views"]),
        "_query": query,
    }


def _headline(summary: dict, top_model=None) -> list[str]:
    """Two or three plain sentences that read the period at a glance."""
    days = summary["days"]
    period = "12 months" if days == 365 else f"{days} days"
    views, change = summary["views"], summary["changes"]["views"]
    lines = []
    if not views:
        return [f"No model views in the last {period} yet."]
    noun = "view" if views == 1 else "views"
    if change["pct"] is None:
        lines.append(f"{views} model {noun} in the last {period}, none in the {period} before.")
    elif change["direction"] == "flat":
        lines.append(f"{views} model {noun}, the same as the previous {period}.")
    else:
        word = "up" if change["direction"] == "up" else "down"
        lines.append(f"{views} model {noun}, {word} {abs(change['pct'])}% on the previous {period}.")
    if top_model is not None:
        lines.append(f"Most viewed: {top_model['name']} ({top_model['views']} {'view' if top_model['views'] == 1 else 'views'}).")
    if summary["sources"]:
        source = summary["sources"][0]
        lines.append(f"Top source: {source['label']} ({source['pct']:g}% of views).")
    return lines


def analytics_snapshot(
    owner_user_id: int | None = None, days: int = 30, project_id: int | None = None, include_trend: bool = True
) -> dict:
    """Return a compact, role-safe dashboard payload from first-party events.

    With ``owner_user_id`` the owner's own activity is excluded, so the numbers
    describe readers only. ``include_trend=False`` skips the previous-period,
    trend and headline work (the admin page only needs totals and audience).
    """
    base = AnalyticsEvent.query
    if owner_user_id is not None:
        base = _own_activity_excluded(base.filter(AnalyticsEvent.owner_user_id == owner_user_id), owner_user_id)
    if project_id is not None:
        base = base.filter(AnalyticsEvent.project_id == project_id)
    summary = _period_summary(base, days, include_trend)
    query = summary.pop("_query")

    top_rows = (
        query.filter(AnalyticsEvent.event_name == "model_viewed", AnalyticsEvent.model_id.isnot(None))
        .with_entities(AnalyticsEvent.model_id, func.count(AnalyticsEvent.id))
        .group_by(AnalyticsEvent.model_id)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .limit(5)
        .all()
    )
    model_ids = [model_id for model_id, _ in top_rows]
    models = {model.id: model for model in Model3D.query.filter(Model3D.id.in_(model_ids)).all()} if model_ids else {}

    projects = []
    model_metrics = []
    if owner_user_id is not None:
        owned_query = Model3D.query.join(Paper).filter(Model3D.user_id == owner_user_id, Paper.deleted_at.is_(None))
        projects = [
            {"id": paper_id, "title": title}
            for paper_id, title in owned_query.with_entities(Paper.id, Paper.title).distinct().order_by(Paper.title)
        ]
        if project_id is not None:
            owned_query = owned_query.filter(Model3D.paper_id == project_id)
        model_metrics = _model_metrics(query, owned_query.order_by(Model3D.created_at.desc()).all())

    leader = next((item for item in model_metrics if item["views"]), None)
    top_model = (
        {"name": leader["model"].display_name or leader["model"].original_filename or "3D model", "views": leader["views"]}
        if leader else None
    )
    return {
        **summary,
        "headline": _headline(summary, top_model) if include_trend else [],
        "project_id": project_id,
        "projects": projects,
        "active_creators": int(
            query.filter(AnalyticsEvent.event_name.in_(("project_created", "model_uploaded")))
            .with_entities(func.count(func.distinct(func.coalesce(AnalyticsEvent.actor_user_id, AnalyticsEvent.owner_user_id))))
            .scalar()
            or 0
        ),
        "top_models": [{"model": models.get(model_id), "count": count_value} for model_id, count_value in top_rows],
        "model_metrics": model_metrics,
    }


def _model_metrics(query, owned_models: list) -> list[dict]:
    """Per-model rows, most viewed first; never-viewed models last."""
    owned_ids = [model.id for model in owned_models]
    if not owned_ids:
        return []
    aggregate: dict = {}
    for model_id, event_name, total, unique_count, last_at in (
        query.with_entities(
            AnalyticsEvent.model_id,
            AnalyticsEvent.event_name,
            func.count(AnalyticsEvent.id),
            func.count(func.distinct(AnalyticsEvent.visitor_hash)),
            func.max(AnalyticsEvent.occurred_at),
        )
        .filter(AnalyticsEvent.model_id.in_(owned_ids))
        .group_by(AnalyticsEvent.model_id, AnalyticsEvent.event_name)
    ):
        aggregate.setdefault(model_id, {})[event_name] = {
            "count": int(total or 0), "unique": int(unique_count or 0), "last_at": last_at,
        }

    internal = _internal_hosts()
    dimension_maps = {}
    for dimension_name, column in (
        ("country", AnalyticsEvent.country_code),
        ("device", AnalyticsEvent.device_type),
        ("source", AnalyticsEvent.referrer_domain),
    ):
        per_model: dict = {}
        rows = (
            query.filter(AnalyticsEvent.event_name == "model_viewed", AnalyticsEvent.model_id.in_(owned_ids))
            .with_entities(AnalyticsEvent.model_id, column, func.count(AnalyticsEvent.id))
            .group_by(AnalyticsEvent.model_id, column)
            .order_by(func.count(AnalyticsEvent.id).desc())
        )
        for model_id, label, count_value in rows:
            if dimension_name == "source":
                label = _source_label(label, internal)
            elif label is None:
                continue
            per_model.setdefault(model_id, {"label": label, "count": int(count_value)})
        dimension_maps[dimension_name] = per_model

    engaged_visitors: dict[str, set[str]] = {}
    for model_id, visitor in (
        query.with_entities(AnalyticsEvent.model_id, AnalyticsEvent.visitor_hash)
        .filter(
            AnalyticsEvent.model_id.in_(owned_ids),
            AnalyticsEvent.event_name.in_(ENGAGEMENT_EVENTS),
            AnalyticsEvent.visitor_hash.isnot(None),
        )
        .distinct()
    ):
        engaged_visitors.setdefault(model_id, set()).add(visitor)

    metrics = []
    for model in owned_models:
        events = aggregate.get(model.id, {})

        def stat(name, key="count"):
            return events.get(name, {}).get(key, 0)

        unique_viewers = stat("model_viewed", "unique")
        engaged_unique = len(engaged_visitors.get(model.id, set()))
        metrics.append({
            "model": model,
            "detailed": plan_supports_feature(model.license_type, "detailed_insights"),
            "views": stat("model_viewed"),
            "unique_visitors": unique_viewers,
            "qr_scans": stat("qr_scanned"),
            "ar_starts": stat("viewer_ar_started"),
            "shares": stat("share_link_copied"),
            "fullscreen_opens": stat("viewer_fullscreen_opened"),
            "rotations": stat("viewer_model_rotated"),
            "engaged_visitors": engaged_unique,
            "engagement_rate": _rate(engaged_unique, unique_viewers),
            "last_viewed_at": events.get("model_viewed", {}).get("last_at"),
            "top_country": dimension_maps["country"].get(model.id),
            "top_device": dimension_maps["device"].get(model.id),
            "top_source": dimension_maps["source"].get(model.id),
        })
    viewed = sorted(
        (item for item in metrics if item["views"]),
        key=lambda item: (-item["views"], -item["unique_visitors"]),
    )
    return viewed + [item for item in metrics if not item["views"]]


def model_snapshot(model, days: int = 30) -> dict:
    """Single-model insights for the owner's per-model detail page."""
    base = _own_activity_excluded(
        AnalyticsEvent.query.filter(AnalyticsEvent.model_id == model.id), model.user_id
    )
    summary = _period_summary(base, days)
    summary.pop("_query")
    summary["detailed"] = plan_supports_feature(model.license_type, "detailed_insights")
    summary["qr_share"] = _rate(summary["qr_scans"], summary["views"])
    # Source names are audience detail: only for plans that include it.
    summary["headline"] = _headline(summary if summary["detailed"] else {**summary, "sources": []})
    return summary
