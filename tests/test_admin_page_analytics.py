from datetime import UTC, datetime, timedelta

from analytics import analytics_snapshot, funnel_snapshot
from models import AnalyticsEvent, User, db
from tests.test_analytics import _public_model


def _admin(client, app, owner_id):
    with app.app_context():
        db.session.get(User, owner_id).is_admin = True
        db.session.commit()
    client.post("/auth/login", data={"email": "owner@example.com", "password": "password123"})


def _seed(app, model_id):
    with app.app_context():
        db.session.add_all([
            AnalyticsEvent(event_name="model_viewed", model_id=model_id, visitor_hash="a", device_type="mobile", country_code="TR"),
            AnalyticsEvent(event_name="model_viewed", model_id=model_id, visitor_hash="b", device_type="desktop", country_code="TR"),
            AnalyticsEvent(event_name="qr_scanned", model_id=model_id, visitor_hash="a"),
            AnalyticsEvent(event_name="viewer_ar_started", model_id=model_id, visitor_hash="a"),
            AnalyticsEvent(event_name="project_created", owner_user_id=7),
            AnalyticsEvent(event_name="model_uploaded", actor_user_id=7),
            AnalyticsEvent(event_name="model_uploaded", owner_user_id=8),
        ])
        db.session.commit()


def test_admin_page_has_four_kpis_and_percentages(client, app):
    owner_id, model_id = _public_model(app)
    _seed(app, model_id)
    _admin(client, app, owner_id)
    body = client.get("/admin/analytics").get_data(as_text=True)
    assert body.count('class="admin-metric"') == 4
    assert "investor" not in body
    assert "(50%)" in body


def test_single_view_is_singular(client, app):
    owner_id, model_id = _public_model(app)
    with app.app_context():
        db.session.add(AnalyticsEvent(event_name="model_viewed", model_id=model_id, visitor_hash="a"))
        db.session.commit()
    _admin(client, app, owner_id)
    body = client.get("/admin/analytics").get_data(as_text=True)
    assert "1 view<" in body and "1 views" not in body


def test_light_snapshot_matches_full_totals_and_skips_trend(app):
    _, model_id = _public_model(app)
    _seed(app, model_id)
    with app.app_context():
        full = analytics_snapshot(days=30)
        light = analytics_snapshot(days=30, include_trend=False)
        for key in ("views", "unique_visitors", "qr_scans", "ar_starts", "projects_created", "models_uploaded", "active_creators", "devices", "countries"):
            assert light[key] == full[key], key
        assert light["views"] == 2 and light["projects_created"] == 1
        assert light["trend"] == [] and full["trend"]
        # coalesce(actor, owner): users 7 and 8.
        assert light["active_creators"] == 2


def test_funnel_uses_one_query_and_calendar_window(app):
    from sqlalchemy import event

    with app.app_context():
        old = datetime.now(UTC) - timedelta(days=30)  # one day outside the 30-calendar-day window
        db.session.add_all([
            AnalyticsEvent(event_name="user_registered", actor_user_id=1),
            AnalyticsEvent(event_name="user_registered", actor_user_id=2, occurred_at=old),
        ])
        db.session.commit()
        statements = []

        def listener(conn, cursor, statement, *rest):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            snapshot = funnel_snapshot(days=30)
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)
        assert len(statements) == 1
        assert snapshot["stages"][0]["count"] == 1
