"""Admins open users' models from the admin panel. A private (but otherwise
valid) project must not answer them with "Page not found"."""
import os
import uuid

from models import Model3D, Paper, User, db
from tests.conftest import create_user, login, register


def _private_model(app):
    with app.app_context():
        owner = User.query.filter_by(email="user@example.com").first()
        paper = Paper(title="Private work", slug=f"priv-{uuid.uuid4().hex[:8]}", user_id=owner.id,
                      is_public=False, visibility="private")
        db.session.add(paper)
        db.session.flush()
        model = Model3D(id=str(uuid.uuid4()), paper_id=paper.id, user_id=owner.id,
                        glb_path="converted/x/model.glb", license_type="academic", processing_status="ready")
        db.session.add(model)
        db.session.commit()
        folder = os.path.join(app.config["CONVERTED_FOLDER"], model.id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "model.glb"), "wb") as fh:
            fh.write(b"glTF")
        return model.id, paper.slug


def _login_admin(client):
    client.post("/auth/logout")
    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin User")
        admin.is_admin = True
        db.session.commit()
    login(client, email="admin@example.com")


def test_admin_can_open_model_of_private_project(client, app):
    register(client)
    model_id, slug = _private_model(app)
    _login_admin(client)
    assert client.get(f"/view/{model_id}").status_code == 200
    assert client.get(f"/files/{model_id}/model.glb").status_code == 200
    assert client.get(f"/p/{slug}").status_code == 200


def test_private_project_stays_hidden_from_other_users(client, app):
    register(client)
    model_id, slug = _private_model(app)
    client.post("/auth/logout")
    register(client, email="other@example.com")
    assert client.get(f"/view/{model_id}").status_code == 403  # "private project" page
    assert client.get(f"/files/{model_id}/model.glb").status_code == 404
    client.post("/auth/logout")
    assert client.get(f"/view/{model_id}").status_code == 403


def test_admin_preview_does_not_count_as_owner_analytics(client, app):
    from models import AnalyticsEvent
    register(client)
    model_id, _ = _private_model(app)
    _login_admin(client)
    client.get(f"/view/{model_id}")
    with app.app_context():
        assert AnalyticsEvent.query.filter_by(model_id=model_id, event_name="model_viewed").count() == 0


def test_admin_models_list_marks_deleted_projects_instead_of_linking_to_404(client, app):
    register(client)
    model_id, _ = _private_model(app)
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        model.paper.status = "deleted"
        db.session.commit()
    _login_admin(client)
    html = client.get("/admin/models").data.decode()
    assert "Project deleted" in html
    assert f'href="/view/{model_id}"' not in html


def test_admin_sees_poster_of_expired_model(client, app):
    """The admin "view as user" dashboard shows the same thumbnails the owner
    sees, including for models whose access window has ended."""
    from datetime import datetime, timedelta
    register(client)
    model_id, _ = _private_model(app)
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        model.paper.visibility = "public"
        model.paper.is_public = True
        model.access_expires_at = datetime.utcnow() - timedelta(days=3)
        folder = os.path.join(app.config["CONVERTED_FOLDER"], model_id)
        with open(os.path.join(folder, "poster.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        model.poster_path = os.path.join(folder, "poster.png")
        db.session.commit()
    client.post("/auth/logout")
    assert client.get(f"/files/{model_id}/poster.png").status_code == 404
    _login_admin(client)
    assert client.get(f"/files/{model_id}/poster.png").status_code == 200
