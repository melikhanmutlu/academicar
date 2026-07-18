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
    assert "Product analytics" in response.get_data(as_text=True)
