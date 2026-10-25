"""Cookieless site traffic for the admin Traffic page.

Every public page sends one small beacon (``templates/_pageview_beacon.html``)
to ``POST /a/pv``, which stores a ``PageView`` row. A visitor is a daily hash
of IP + user agent (salt derived from SECRET_KEY and the date), so nothing is
stored on the visitor's device and no consent banner is needed. The trade-off:
someone who comes back on another day counts again, so "unique visitors" over
a period is the sum of daily unique visitors.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from flask import current_app, request, session
from flask_login import current_user
from sqlalchemy import func

from analytics import _internal_hosts, _request_country, device_type, ga_content_group
from models import Model3D, PageView, Paper, db

QR_SESSION_KEY = "pv_qr"
DEDUP_SECONDS = 30
MAX_PATH = 255

# Paths that are not pages people read.
_SKIPPED_PREFIXES = (
    "/admin", "/static", "/files", "/api", "/a/", "/analytics", "/health", "/qr-image",
    "/uploads", "/media", "/converted", "/blog-images", "/institution-logos",
)
_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|scrape|headless|lighthouse|pagespeed|preview|monitor|uptime|"
    r"facebookexternalhit|embedly|curl|wget|python|httpx|java/|go-http|okhttp|phantom|"
    r"selenium|playwright|puppeteer",
    re.I,
)

# (domain suffix, source label, channel). First match wins, so the more
# specific hosts (gemini.google.com) come before the general ones (google.).
_KNOWN_SOURCES = (
    ("chatgpt.com", "ChatGPT", "AI assistants"),
    ("chat.openai.com", "ChatGPT", "AI assistants"),
    ("perplexity.ai", "Perplexity", "AI assistants"),
    ("claude.ai", "Claude", "AI assistants"),
    ("gemini.google.com", "Gemini", "AI assistants"),
    ("copilot.microsoft.com", "Copilot", "AI assistants"),
    ("mail.google.com", "Gmail", "Email"),
    ("outlook.live.com", "Outlook", "Email"),
    ("outlook.office.com", "Outlook", "Email"),
    ("outlook.office365.com", "Outlook", "Email"),
    ("mail.yahoo.com", "Yahoo Mail", "Email"),
    ("google.", "Google", "Search"),
    ("bing.com", "Bing", "Search"),
    ("duckduckgo.com", "DuckDuckGo", "Search"),
    ("yandex.", "Yandex", "Search"),
    ("search.yahoo.com", "Yahoo", "Search"),
    ("ecosia.org", "Ecosia", "Search"),
    ("search.brave.com", "Brave Search", "Search"),
    ("baidu.com", "Baidu", "Search"),
    ("linkedin.com", "LinkedIn", "Social"),
    ("lnkd.in", "LinkedIn", "Social"),
    ("t.co", "X (Twitter)", "Social"),
    ("x.com", "X (Twitter)", "Social"),
    ("twitter.com", "X (Twitter)", "Social"),
    ("facebook.com", "Facebook", "Social"),
    ("fb.com", "Facebook", "Social"),
    ("instagram.com", "Instagram", "Social"),
    ("reddit.com", "Reddit", "Social"),
    ("youtube.com", "YouTube", "Social"),
    ("bsky.app", "Bluesky", "Social"),
    ("threads.net", "Threads", "Social"),
    ("whatsapp.com", "WhatsApp", "Social"),
    ("web.telegram.org", "Telegram", "Social"),
    ("researchgate.net", "ResearchGate", "Referral"),
    ("github.com", "GitHub", "Referral"),
)
CHANNELS = ("Search", "AI assistants", "Social", "Email", "Referral", "Campaign", "QR code", "Direct")


def _match_known(host: str) -> tuple[str, str] | None:
    host = host.lower().removeprefix("www.").removeprefix("m.")
    for suffix, label, channel in _KNOWN_SOURCES:
        if suffix.endswith("."):
            if host.startswith(suffix) or f".{suffix}" in f".{host}":
                return label, channel
        elif host == suffix or host.endswith(f".{suffix}"):
            return label, channel
    return None


def classify_source(referrer_host: str | None, utm: dict, from_qr: bool = False) -> tuple[str, str]:
    """(channel, source) for the first page of a visit."""
    utm_source = (utm.get("source") or "").strip().lower()
    utm_medium = (utm.get("medium") or "").strip().lower()
    if from_qr or utm_medium == "qr" or utm_source == "qr":
        return "QR code", "QR code"
    if utm_source:
        known = _match_known(utm_source if "." in utm_source else f"{utm_source}.com")
        if utm_medium in {"email", "e-mail", "newsletter"}:
            return "Email", known[0] if known else utm_source
        if known:
            return known[1], known[0]
        return "Campaign", utm_source[:120]
    if referrer_host:
        known = _match_known(referrer_host)
        if known:
            return known[1], known[0]
        return "Referral", referrer_host.lower().removeprefix("www.")[:120]
    return "Direct", "Direct"


def is_bot(user_agent: str | None) -> bool:
    return not user_agent or bool(_BOT_RE.search(user_agent))


def daily_visitor_hash(day: date | None = None) -> str:
    day = day or datetime.now(UTC).date()
    secret = (current_app.config.get("SECRET_KEY") or "academicar").encode()
    salt = hmac.new(secret, f"pageview:{day.isoformat()}".encode(), hashlib.sha256).hexdigest()
    ua = request.user_agent.string or ""
    return hashlib.sha256(f"{salt}:{request.remote_addr or ''}:{ua}".encode()).hexdigest()


def mark_qr_visit() -> None:
    """The QR resolver redirects to the viewer; remember it so the next page
    view is credited to the QR code."""
    session[QR_SESSION_KEY] = True


def clean_path(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str) or not raw.startswith("/") or raw.startswith("//"):
        return None
    path = raw.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 1:
        path = path.rstrip("/")
    if path.startswith(_SKIPPED_PREFIXES):
        return None
    return path[:MAX_PATH]


def _utm_from(search: str | None) -> dict:
    if not search or not isinstance(search, str):
        return {}
    params = parse_qs(search.lstrip("?")[:1000])
    return {
        key: (params.get(f"utm_{key}") or [""])[0].strip()[:120]
        for key in ("source", "medium", "campaign")
        if (params.get(f"utm_{key}") or [""])[0].strip()
    }


def record_page_view(payload: dict) -> str:
    """Store one page view; returns "ok" or why it was skipped."""
    if is_bot(request.user_agent.string):
        return "bot"
    if current_user.is_authenticated and getattr(current_user, "is_admin", False):
        return "admin"
    path = clean_path(payload.get("p"))
    if not path:
        return "path"
    from_qr = bool(session.pop(QR_SESSION_KEY, False))
    utm = _utm_from(payload.get("q"))
    referrer = payload.get("r") if isinstance(payload.get("r"), str) else ""
    referrer_host = (urlparse(referrer[:1000]).hostname or "").lower() or None
    internal = bool(referrer_host) and (
        referrer_host in _internal_hosts() or referrer_host.endswith(".up.railway.app")
    )
    # Internal links carry UTM tags too (blog CTAs), so only an arrival from
    # outside the site starts a visit.
    is_entry = not internal or from_qr
    if not is_entry:
        utm = {}
    visitor = daily_visitor_hash()
    now = datetime.now(UTC).replace(tzinfo=None)
    # A reload or a double-sent beacon is not another view.
    recent = PageView.query.filter(
        PageView.visitor_hash == visitor,
        PageView.path == path,
        PageView.occurred_at >= now - timedelta(seconds=DEDUP_SECONDS),
    ).first()
    if recent is not None:
        return "duplicate"
    channel = source = None
    if is_entry:
        channel, source = classify_source(None if internal else referrer_host, utm, from_qr)
    country = _request_country()
    db.session.add(PageView(
        occurred_at=now,
        path=path,
        section=ga_content_group(path),
        visitor_hash=visitor,
        is_entry=is_entry,
        channel=channel,
        source=source,
        referrer_domain=None if internal or not referrer_host else referrer_host[:255],
        utm_source=utm.get("source"),
        utm_medium=utm.get("medium"),
        utm_campaign=utm.get("campaign"),
        country_code=country if len(country) == 2 and country.isalpha() else None,
        device_type=device_type(),
        signed_in=bool(current_user.is_authenticated),
    ))
    db.session.commit()
    return "ok"


# ---------------------------------------------------------------- reporting

_SECTION_LABELS = {
    "home": "Home", "blog": "Blog", "use_case": "Use-case pages", "pricing": "Pricing",
    "institutional": "Institutional", "viewer": "3D viewer", "demo": "Demos", "project": "Projects",
    "institution": "Institution pages", "auth": "Sign up / sign in", "app": "Signed-in app", "other": "Other",
}


def _uniques(query) -> int:
    return int(query.with_entities(func.count(func.distinct(PageView.visitor_hash))).scalar() or 0)


def _grouped(query, column, limit: int | None = 10) -> list[dict]:
    rows = (
        query.with_entities(
            column,
            func.count(func.distinct(PageView.visitor_hash)).label("visitors"),
            func.count(PageView.id).label("views"),
        )
        .group_by(column)
        .order_by(func.count(func.distinct(PageView.visitor_hash)).desc(), func.count(PageView.id).desc())
    )
    if limit:
        rows = rows.limit(limit)
    return [{"label": label, "visitors": int(v or 0), "views": int(n or 0)} for label, v, n in rows.all()]


def _with_share(rows: list[dict], total: int) -> list[dict]:
    for row in rows:
        row["pct"] = round(row["visitors"] / total * 100, 1) if total else 0
    return rows


def _page_titles(paths: list[str]) -> dict[str, str]:
    """Readable names for viewer and project pages (their paths are ids)."""
    titles: dict[str, str] = {}
    model_ids = {p.split("/")[2] for p in paths if p.startswith("/view/") and p.count("/") == 2}
    if model_ids:
        for model in Model3D.query.filter(Model3D.id.in_(model_ids)).all():
            titles[f"/view/{model.id}"] = model.display_name or model.original_filename or "3D model"
    slugs = {p.split("/")[2] for p in paths if p.startswith("/p/") and p.count("/") == 2}
    if slugs:
        for paper in Paper.query.filter(Paper.slug.in_(slugs)).all():
            titles[f"/p/{paper.slug}"] = paper.title
    return titles


def _change(current: int, previous: int) -> dict:
    pct = round((current - previous) / previous * 100) if previous else None
    direction = "up" if current > previous else "down" if current < previous else "flat"
    return {"previous": previous, "pct": pct, "direction": direction}


def traffic_snapshot(days: int = 30, now: datetime | None = None) -> dict:
    days = days if days in (7, 30, 90, 365) else 30
    now = (now or datetime.now(UTC)).replace(tzinfo=None)
    first_day = now.date() - timedelta(days=days - 1)
    start = datetime(first_day.year, first_day.month, first_day.day)
    prev_start = start - timedelta(days=days)

    current = PageView.query.filter(PageView.occurred_at >= start)
    previous = PageView.query.filter(PageView.occurred_at >= prev_start, PageView.occurred_at < start)
    entries = current.filter(PageView.is_entry.is_(True))

    views = int(current.with_entities(func.count(PageView.id)).scalar() or 0)
    visitors = _uniques(current)
    visits = int(entries.with_entities(func.count(PageView.id)).scalar() or 0)
    prev_views = int(previous.with_entities(func.count(PageView.id)).scalar() or 0)
    prev_visitors = _uniques(previous)
    prev_visits = int(previous.filter(PageView.is_entry.is_(True)).with_entities(func.count(PageView.id)).scalar() or 0)
    live = _uniques(PageView.query.filter(PageView.occurred_at >= now - timedelta(minutes=30)))
    today_start = datetime(now.year, now.month, now.day)
    today_visitors = _uniques(PageView.query.filter(PageView.occurred_at >= today_start))

    # Daily (or weekly for 90 days / monthly for 12 months) visitors and views.
    granularity = "day" if days <= 31 else "week" if days <= 120 else "month"
    if granularity == "day":
        starts = [first_day + timedelta(days=i) for i in range(days)]
    elif granularity == "week":
        starts = [first_day + timedelta(days=i) for i in range(0, days, 7)]
    else:
        starts, cursor = [], first_day.replace(day=1)
        while cursor <= now.date():
            starts.append(cursor)
            cursor = (cursor + timedelta(days=32)).replace(day=1)
    fmt = "%b %Y" if granularity == "month" else "%b %d"
    points = [{"label": s.strftime(fmt), "start": s.isoformat(), "visitors": 0, "views": 0} for s in starts]
    day_rows = (
        current.with_entities(
            func.date(PageView.occurred_at),
            func.count(func.distinct(PageView.visitor_hash)),
            func.count(PageView.id),
        )
        .group_by(func.date(PageView.occurred_at))
        .all()
    )
    for day_value, day_visitors, day_views in day_rows:
        day = date.fromisoformat(str(day_value)[:10])
        index = max(i for i, s in enumerate(starts) if s <= day) if day >= starts[0] else -1
        if index >= 0:
            # Visitor hashes change daily, so summing days is exact.
            points[index]["visitors"] += int(day_visitors or 0)
            points[index]["views"] += int(day_views or 0)
    # Axis tops rounded up to even, so the middle gridline is a whole number.
    trend_max = max([p["visitors"] for p in points] + [1])
    trend_max += trend_max % 2 if trend_max >= 4 else 0
    trend_max_views = max([p["views"] for p in points] + [1])
    trend_max_views += trend_max_views % 2 if trend_max_views >= 4 else 0

    pages = _grouped(current, PageView.path, limit=25)
    titles = _page_titles([row["label"] for row in pages])
    for row in pages:
        row["title"] = titles.get(row["label"])
    entry_pages = _grouped(entries, PageView.path, limit=10)
    entry_titles = _page_titles([row["label"] for row in entry_pages])
    for row in entry_pages:
        row["title"] = entry_titles.get(row["label"])
    sections = _grouped(current, PageView.section, limit=None)
    for row in sections:
        row["label"] = _SECTION_LABELS.get(row["label"] or "other", (row["label"] or "Other").title())

    entry_visitors = _uniques(entries)
    channels = _with_share(_grouped(entries, PageView.channel, limit=None), entry_visitors)
    sources = _with_share(_grouped(entries, PageView.source, limit=10), entry_visitors)
    countries = _with_share(
        [r for r in _grouped(current, PageView.country_code, limit=None) if r["label"]][:10], visitors
    )
    devices = _with_share(_grouped(current, PageView.device_type, limit=None), visitors)
    campaign_rows = (
        entries.filter(PageView.utm_campaign.isnot(None))
        .with_entities(
            PageView.utm_campaign, PageView.utm_source, PageView.utm_medium,
            func.count(func.distinct(PageView.visitor_hash)), func.count(PageView.id),
        )
        .group_by(PageView.utm_campaign, PageView.utm_source, PageView.utm_medium)
        .order_by(func.count(func.distinct(PageView.visitor_hash)).desc())
        .limit(6)
        .all()
    )
    campaigns = [
        {"campaign": c, "source": s, "medium": m, "visitors": int(v or 0), "visits": int(n or 0)}
        for c, s, m, v, n in campaign_rows
    ]
    signed_in = _uniques(current.filter(PageView.signed_in.is_(True)))

    return {
        "days": days,
        "views": views,
        "visitors": visitors,
        "visits": visits,
        "pages_per_visit": round(views / visits, 1) if visits else None,
        "live": live,
        "today_visitors": today_visitors,
        "signed_in_visitors": signed_in,
        "changes": {
            "views": _change(views, prev_views),
            "visitors": _change(visitors, prev_visitors),
            "visits": _change(visits, prev_visits),
        },
        "granularity": granularity,
        "trend": points,
        "trend_max": trend_max,
        "trend_max_views": trend_max_views,
        "pages": pages,
        "entry_pages": entry_pages,
        "sections": _with_share(sections, visitors),
        "channels": channels,
        "sources": sources,
        "countries": countries,
        "devices": devices,
        "campaigns": campaigns,
        "has_data": bool(views),
        "tracking_since": db.session.query(func.min(PageView.occurred_at)).scalar(),
    }
