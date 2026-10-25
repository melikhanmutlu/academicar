"""Cookieless site traffic: the /a/pv beacon, source classification and the
admin Traffic page."""
import json
from datetime import UTC, datetime, timedelta

from models import PageView, User, db
from tests.test_admin_page_analytics import _admin
from tests.test_analytics import _public_model
from traffic import classify_source, clean_path, traffic_snapshot

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"


def _beacon(client, path, referrer="", search="", ua=UA, ip="203.0.113.5"):
    return client.post(
        "/a/pv",
        data=json.dumps({"p": path, "q": search, "r": referrer}),
        content_type="text/plain;charset=UTF-8",
        headers={"User-Agent": ua, "CF-IPCountry": "DE"},
        environ_base={"REMOTE_ADDR": ip},
    )


def test_beacon_records_a_visit_with_source_country_and_device(client, app):
    assert _beacon(client, "/blog/open-step-stp-file-online", referrer="https://www.google.com/").status_code == 204
    assert _beacon(client, "/pricing", referrer="http://localhost/blog/open-step-stp-file-online").status_code == 204
    with app.app_context():
        rows = PageView.query.order_by(PageView.id).all()
        assert [r.path for r in rows] == ["/blog/open-step-stp-file-online", "/pricing"]
        entry, inner = rows
        assert entry.is_entry and entry.channel == "Search" and entry.source == "Google"
        assert entry.section == "blog" and entry.country_code == "DE" and entry.device_type == "mobile"
        assert not inner.is_entry and inner.channel is None and inner.referrer_domain is None
        assert entry.visitor_hash == inner.visitor_hash and len(entry.visitor_hash) == 64
        # No raw IP or user agent is stored.
        assert "203.0.113.5" not in json.dumps([r.visitor_hash for r in rows])


def test_reload_is_not_counted_twice_and_other_devices_are_new_visitors(client, app):
    _beacon(client, "/")
    _beacon(client, "/")
    _beacon(client, "/", ip="198.51.100.7")
    with app.app_context():
        assert PageView.query.count() == 2
        assert len({r.visitor_hash for r in PageView.query.all()}) == 2


def test_bots_admin_and_non_pages_are_skipped(client, app):
    assert _beacon(client, "/", ua="Mozilla/5.0 (compatible; Googlebot/2.1)").status_code == 202
    assert _beacon(client, "/", ua="").status_code == 202
    for path in ("/admin", "/static/x.css", "//evil.com", "https://evil.com/", None):
        assert _beacon(client, path).status_code == 202
    owner_id, _ = _public_model(app)
    _admin(client, app, owner_id)
    assert _beacon(client, "/").get_json() == {"ignored": "admin"}
    with app.app_context():
        assert PageView.query.count() == 0


def test_qr_scan_credits_the_viewer_visit_to_qr(client, app):
    from models import QRLink
    owner_id, model_id = _public_model(app)
    with app.app_context():
        link = QRLink.query.filter_by(model_id=model_id).first()
        if link is None:
            link = QRLink(public_id="qrtest01", model_id=model_id)
            db.session.add(link)
            db.session.commit()
        public_id = link.public_id
    client.get(f"/m/{public_id}")
    _beacon(client, f"/view/{model_id}")
    with app.app_context():
        row = PageView.query.one()
        assert row.channel == "QR code" and row.is_entry


def test_internal_utm_links_do_not_start_a_visit(client, app):
    _beacon(client, "/auth/register", referrer="http://localhost/blog/x", search="?utm_source=blog&utm_campaign=x")
    _beacon(client, "/pricing", search="?utm_source=linkedin&utm_medium=social&utm_campaign=launch")
    with app.app_context():
        inner, entry = PageView.query.order_by(PageView.id).all()
        assert not inner.is_entry and inner.utm_campaign is None
        assert entry.is_entry and entry.channel == "Social" and entry.source == "LinkedIn"
        assert entry.utm_campaign == "launch"


def test_classify_source():
    assert classify_source("chatgpt.com", {}) == ("AI assistants", "ChatGPT")
    assert classify_source("gemini.google.com", {}) == ("AI assistants", "Gemini")
    assert classify_source("www.google.com.tr", {}) == ("Search", "Google")
    assert classify_source("l.facebook.com", {}) == ("Social", "Facebook")
    assert classify_source("t.co", {}) == ("Social", "X (Twitter)")
    assert classify_source("www.researchgate.net", {}) == ("Referral", "ResearchGate")
    assert classify_source("uni-example.edu", {}) == ("Referral", "uni-example.edu")
    assert classify_source(None, {}) == ("Direct", "Direct")
    assert classify_source(None, {"source": "newsletter", "medium": "email"}) == ("Email", "newsletter")
    assert classify_source(None, {"source": "poster"}) == ("Campaign", "poster")
    assert classify_source(None, {}, from_qr=True) == ("QR code", "QR code")


def test_clean_path():
    assert clean_path("/blog/") == "/blog"
    assert clean_path("/") == "/"
    assert clean_path("/pricing?x=1") == "/pricing"
    assert clean_path("/files/abc/model.glb") is None


def _row(app, hash_, path, when, entry=True, channel="Search", source="Google", country="TR", device="desktop"):
    db.session.add(PageView(
        occurred_at=when, path=path, section="blog" if path.startswith("/blog") else "home",
        visitor_hash=hash_, is_entry=entry, channel=channel if entry else None,
        source=source if entry else None, country_code=country, device_type=device,
    ))


def test_snapshot_counts_unique_visitors_per_page(app):
    now = datetime.now(UTC).replace(tzinfo=None)
    with app.app_context():
        _row(app, "a", "/", now)
        _row(app, "a", "/blog/x", now, entry=False)
        _row(app, "b", "/blog/x", now, channel="Direct", source="Direct", device="mobile")
        _row(app, "c", "/blog/x", now - timedelta(days=40))  # previous period
        db.session.commit()
        snap = traffic_snapshot(30)
    assert (snap["visitors"], snap["visits"], snap["views"]) == (2, 2, 3)
    assert snap["pages"][0] == {"label": "/blog/x", "visitors": 2, "views": 2, "title": None}
    assert {c["label"]: c["visitors"] for c in snap["channels"]} == {"Search": 1, "Direct": 1}
    assert snap["trend"][-1]["visitors"] == 2
    assert snap["changes"]["visitors"]["direction"] == "up"
    assert snap["live"] == 2


def test_admin_traffic_page_renders(client, app):
    owner_id, model_id = _public_model(app)
    now = datetime.now(UTC).replace(tzinfo=None)
    with app.app_context():
        _row(app, "a", f"/view/{model_id}", now)
        _row(app, "b", "/blog/x", now, channel="AI assistants", source="ChatGPT")
        db.session.commit()
    _admin(client, app, owner_id)
    body = client.get("/admin/traffic?days=7").get_data(as_text=True)
    assert "Site traffic" in body and "Analytics model" in body and "ChatGPT" in body
    assert "AI assistants" in body and 'class="ins-kpi"' in body
    # The admin page itself sends no beacon.
    assert "/a/pv" not in body
    # Public pages do.
    client.get("/auth/logout")
    assert "/a/pv" in client.get("/").get_data(as_text=True)


def test_error_pages_send_no_beacon(client):
    resp = client.get("/no-such-page-xyz")
    assert resp.status_code == 404
    assert "/a/pv" not in resp.get_data(as_text=True)
