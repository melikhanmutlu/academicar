"""Insights rework: own-activity exclusion, sources, trend buckets, changes,
engagement, project filter and the per-model detail page."""
import uuid
from datetime import UTC, datetime, timedelta

from analytics import DIRECT_SOURCE_LABEL, INTERNAL_SOURCE_LABEL, analytics_snapshot, model_snapshot
from models import AnalyticsEvent, Model3D, Paper, User, db
from tests.conftest import login, register


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


def _setup(app, email="user@example.com", title="Insight project"):
    with app.app_context():
        owner = User.query.filter_by(email=email).one()
        paper = Paper(title=title, slug=f"ins-{uuid.uuid4().hex[:8]}", user_id=owner.id,
                      visibility="public", is_public=True)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(id=str(uuid.uuid4()), paper_id=paper.id, user_id=owner.id, display_name="Femur",
                        glb_path="x.glb", license_type="academic", processing_status="ready")
        db.session.add(model)
        db.session.commit()
        return owner.id, paper.id, model.id


def _event(owner_id, model_id, name="model_viewed", visitor="v1", when=None, **extra):
    db.session.add(AnalyticsEvent(event_name=name, owner_user_id=owner_id, model_id=model_id,
                                  visitor_hash=visitor, occurred_at=when or _now(), **extra))


def test_owner_activity_excluded_even_when_logged_out(client, app):
    register(client)
    owner_id, _, model_id = _setup(app)
    with app.app_context():
        # The owner's browser (visitor "own") once acted while signed in...
        _event(owner_id, model_id, name="share_link_copied", visitor="own", actor_user_id=owner_id)
        # ...so its later logged-out views are not reader views.
        _event(owner_id, model_id, visitor="own")
        _event(owner_id, model_id, visitor="own")
        _event(owner_id, model_id, visitor="reader")
        db.session.commit()
        snapshot = analytics_snapshot(owner_id, days=30)
    assert snapshot["views"] == 1 and snapshot["unique_visitors"] == 1
    assert snapshot["shares"] == 0
    assert snapshot["model_metrics"][0]["views"] == 1


def test_sources_group_direct_and_internal_traffic(client, app):
    register(client)
    owner_id, _, model_id = _setup(app)
    app.config["SITE_URL"] = "https://academicar.com"
    with app.app_context():
        _event(owner_id, model_id, visitor="a")
        _event(owner_id, model_id, visitor="b", referrer_domain="academicar.com")
        _event(owner_id, model_id, visitor="c", referrer_domain="academicar.up.railway.app")
        _event(owner_id, model_id, visitor="d", referrer_domain="www.google.com")
        db.session.commit()
        sources = {row["label"]: row for row in analytics_snapshot(owner_id, days=30)["sources"]}
    assert sources[DIRECT_SOURCE_LABEL]["count"] == 1
    assert sources[INTERNAL_SOURCE_LABEL]["count"] == 2 and sources[INTERNAL_SOURCE_LABEL]["pct"] == 50.0
    assert sources["google.com"]["count"] == 1


def test_trend_buckets_by_period(client, app):
    register(client)
    owner_id, _, model_id = _setup(app)
    with app.app_context():
        for days_ago in (0, 10, 40, 200):
            _event(owner_id, model_id, visitor=f"v{days_ago}", when=_now() - timedelta(days=days_ago))
        db.session.commit()
        week = analytics_snapshot(owner_id, days=7)
        month = analytics_snapshot(owner_id, days=30)
        quarter = analytics_snapshot(owner_id, days=90)
        year = analytics_snapshot(owner_id, days=365)
    assert week["trend_granularity"] == "day" and len(week["trend"]) == 7
    assert month["trend_granularity"] == "day" and len(month["trend"]) == 30
    assert quarter["trend_granularity"] == "week" and len(quarter["trend"]) == 13
    assert year["trend_granularity"] == "month" and 12 <= len(year["trend"]) <= 13
    assert sum(p["views"] for p in quarter["trend"]) == quarter["views"] == 3
    assert sum(p["views"] for p in year["trend"]) == year["views"] == 4
    assert month["trend_peak"]["views"] == 1


def test_change_against_previous_period(client, app):
    register(client)
    owner_id, _, model_id = _setup(app)
    with app.app_context():
        for i in range(3):
            _event(owner_id, model_id, visitor=f"now{i}")
        for i in range(2):
            _event(owner_id, model_id, visitor=f"old{i}", when=_now() - timedelta(days=10))
        db.session.commit()
        snapshot = analytics_snapshot(owner_id, days=7)
    assert snapshot["changes"]["views"] == {"previous": 2, "pct": 50, "direction": "up"}
    assert snapshot["changes"]["qr_scans"]["pct"] is None


def test_engagement_counts_rotation_and_is_none_without_viewers(client, app):
    register(client)
    owner_id, _, model_id = _setup(app)
    with app.app_context():
        snapshot = analytics_snapshot(owner_id, days=30)
        assert snapshot["engagement_rate"] is None
        assert snapshot["model_metrics"][0]["engagement_rate"] is None
        _event(owner_id, model_id, visitor="a")
        _event(owner_id, model_id, visitor="b")
        _event(owner_id, model_id, name="viewer_model_rotated", visitor="a")
        db.session.commit()
        snapshot = analytics_snapshot(owner_id, days=30)
    assert snapshot["engagement_rate"] == 50.0
    assert snapshot["model_metrics"][0]["engagement_rate"] == 50.0
    assert [step["count"] for step in snapshot["funnel"]] == [2, 1, 0]


def test_rotation_browser_event_is_accepted(client, app):
    register(client)
    _, _, model_id = _setup(app)
    client.post("/auth/logout")
    response = client.post("/analytics/event", json={"event": "viewer_model_rotated", "model_id": model_id})
    assert response.status_code == 202
    with app.app_context():
        assert AnalyticsEvent.query.filter_by(event_name="viewer_model_rotated").count() == 1


def test_model_metrics_sorted_by_views_unviewed_last(client, app):
    register(client)
    owner_id, paper_id, first_id = _setup(app)
    with app.app_context():
        second = Model3D(id=str(uuid.uuid4()), paper_id=paper_id, user_id=owner_id, display_name="Tibia",
                         glb_path="y.glb", license_type="academic", processing_status="ready")
        unseen = Model3D(id=str(uuid.uuid4()), paper_id=paper_id, user_id=owner_id, display_name="Unseen",
                         glb_path="z.glb", license_type="academic", processing_status="ready")
        db.session.add_all([second, unseen])
        db.session.flush()
        _event(owner_id, first_id, visitor="a")
        _event(owner_id, second.id, visitor="a")
        _event(owner_id, second.id, visitor="b")
        db.session.commit()
        names = [item["model"].display_name for item in analytics_snapshot(owner_id, days=30)["model_metrics"]]
    assert names == ["Tibia", "Femur", "Unseen"]


def test_project_filter_limits_numbers_and_ignores_foreign_projects(client, app):
    register(client)
    owner_id, paper_a, model_a = _setup(app, title="Alpha")
    _, paper_b, model_b = _setup(app, title="Beta")
    with app.app_context():
        _event(owner_id, model_a, visitor="a", project_id=paper_a)
        _event(owner_id, model_b, visitor="b", project_id=paper_b)
        _event(owner_id, model_b, visitor="c", project_id=paper_b)
        db.session.commit()
        snapshot = analytics_snapshot(owner_id, days=30, project_id=paper_b)
    assert snapshot["views"] == 2
    assert [item["model"].id for item in snapshot["model_metrics"]] == [model_b]
    assert {p["title"] for p in snapshot["projects"]} == {"Alpha", "Beta"}
    assert client.get(f"/insights?project={paper_b}").status_code == 200
    assert client.get("/insights?project=999999").status_code == 200


def test_model_detail_page_owner_only(client, app):
    register(client)
    owner_id, _, model_id = _setup(app)
    with app.app_context():
        _event(owner_id, model_id, visitor="a")
        _event(owner_id, model_id, name="qr_scanned", visitor="a")
        db.session.commit()
        model = db.session.get(Model3D, model_id)
        snapshot = model_snapshot(model, days=30)
    assert snapshot["views"] == 1 and snapshot["qr_share"] == 100.0
    assert client.get(f"/insights/model/{model_id}").status_code == 200
    client.post("/auth/logout")
    register(client, email="other@example.com")
    login(client, email="other@example.com")
    assert client.get(f"/insights/model/{model_id}").status_code == 404
    assert client.get("/insights/model/not-a-uuid").status_code == 404


def test_previous_period_trend_and_headline(client, app):
    register(client)
    owner_id, _, model_id = _setup(app)
    with app.app_context():
        for i in range(3):
            _event(owner_id, model_id, visitor=f"now{i}")
        _event(owner_id, model_id, visitor="old", when=_now() - timedelta(days=7))
        db.session.commit()
        snapshot = analytics_snapshot(owner_id, days=7)
    assert sum(point["previous_views"] for point in snapshot["trend"]) == 1
    assert snapshot["trend_max"] == 3
    assert snapshot["headline"][0] == "3 model views, up 200% on the previous 7 days."
    assert snapshot["headline"][1] == "Most viewed: Femur (3 views)."
    assert snapshot["headline"][2] == "Top source: Direct / QR (100% of views)."


def test_headline_without_views(client, app):
    register(client)
    owner_id, _, _ = _setup(app)
    with app.app_context():
        snapshot = analytics_snapshot(owner_id, days=30)
    assert snapshot["headline"] == ["No model views in the last 30 days yet."]


def test_engagement_comparison_hidden_before_full_tracking(client, app):
    import analytics

    register(client)
    owner_id, _, _ = _setup(app)
    with app.app_context():
        early = analytics_snapshot(owner_id, days=7)
        assert early["changes"]["engaged_visitors"] is None and early["engagement_partial"] is True
        original = analytics.ENGAGEMENT_TRACKED_SINCE
        analytics.ENGAGEMENT_TRACKED_SINCE = (_now() - timedelta(days=400)).date()
        try:
            later = analytics_snapshot(owner_id, days=7)
        finally:
            analytics.ENGAGEMENT_TRACKED_SINCE = original
    assert later["changes"]["engaged_visitors"] is not None and later["engagement_partial"] is False


def test_model_headline_hides_sources_without_detailed_plan(client, app, monkeypatch):
    monkeypatch.setattr("analytics.plan_supports_feature", lambda plan, feature: False)
    register(client)
    owner_id, _, model_id = _setup(app)
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        _event(owner_id, model_id, visitor="a", referrer_domain="google.com")
        db.session.commit()
        snapshot = model_snapshot(model, days=30)
    assert snapshot["detailed"] is False
    assert not any("source" in line.lower() for line in snapshot["headline"])
