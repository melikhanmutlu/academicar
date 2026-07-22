from uuid import uuid4

from models import AnalyticsEvent, Model3D, Paper, User, db


def _create_user(email="owner@example.com", username="Owner"):
    user = User(email=email, username=username)
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


def _public_model(app):
    with app.app_context():
        owner = _create_user()
        project = Paper(
            title="Analytics project",
            slug="analytics-project",
            user_id=owner.id,
            visibility="public",
            is_public=True,
        )
        db.session.add(project)
        db.session.flush()
        model = Model3D(
            id=str(uuid4()),
            paper_id=project.id,
            user_id=owner.id,
            display_name="Analytics model",
            glb_path="analytics.glb",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()
        return owner.id, model.id


def test_public_model_view_records_pseudonymous_analytics(client, app):
    _, model_id = _public_model(app)

    response = client.get(f"/view/{model_id}")
    assert response.status_code == 200
    assert "aar_vid=" in response.headers.get("Set-Cookie", "")
    with app.app_context():
        event = AnalyticsEvent.query.filter_by(event_name="model_viewed", model_id=model_id).one()
        assert event.visitor_hash
        assert event.owner_user_id is not None
        assert event.properties == {}


def test_owner_and_admin_can_open_analytics(client, app):
    owner_id, model_id = _public_model(app)
    client.get(f"/view/{model_id}")
    client.post("/auth/login", data={"email": "owner@example.com", "password": "password123"})
    response = client.get("/insights")
    assert response.status_code == 200
    assert "Model views" in response.get_data(as_text=True)

    with app.app_context():
        owner = db.session.get(User, owner_id)
        owner.is_admin = True
        db.session.commit()
    response = client.get("/admin/analytics")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Product analytics" in body
    assert "Conversion funnel" in body


def test_registration_emits_user_registered_funnel_event(client, app):
    from tests.conftest import register

    register(client, email="funnel@example.com")
    with app.app_context():
        event = AnalyticsEvent.query.filter_by(event_name="user_registered").one()
        user = User.query.filter_by(email="funnel@example.com").one()
        # Fired server-side at signup: the new user is the funnel subject.
        assert event.owner_user_id == user.id or event.actor_user_id == user.id


def test_funnel_snapshot_orders_stages_and_counts_distinct_users(app):
    from analytics import FUNNEL_STAGES, funnel_snapshot

    with app.app_context():
        # Two users sign up; one uploads a model; nobody pays.
        db.session.add_all([
            AnalyticsEvent(event_name="user_registered", actor_user_id=1),
            AnalyticsEvent(event_name="user_registered", actor_user_id=2),
            # Duplicate signup event for user 1 must not double-count the stage.
            AnalyticsEvent(event_name="user_registered", actor_user_id=1),
            AnalyticsEvent(event_name="model_uploaded", actor_user_id=1),
            # Webhook-style event with no actor: owner is the funnel subject.
            AnalyticsEvent(event_name="model_uploaded", owner_user_id=2),
        ])
        db.session.commit()

        snapshot = funnel_snapshot(days=30)
        stages = {s["event"]: s for s in snapshot["stages"]}
        # Stages come back in the canonical funnel order.
        assert [s["event"] for s in snapshot["stages"]] == [name for name, _ in FUNNEL_STAGES]
        # Distinct users, not raw event volume (user 1 signed up twice -> counts once).
        assert stages["user_registered"]["count"] == 2
        assert stages["model_uploaded"]["count"] == 2
        assert stages["payment_succeeded"]["count"] == 0
        # Top-of-funnel percentage is relative to the first stage.
        assert stages["user_registered"]["pct_of_top"] == 100.0
        assert stages["model_uploaded"]["pct_of_top"] == 100.0
