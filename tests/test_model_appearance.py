"""Tests for the model appearance (save color) flow: cache-busting token on the
served GLB, and redirecting back to the submitting page via a safe ``next``."""
import uuid
from datetime import UTC, datetime, timedelta

from models import Model3D, Paper, User, db


def _owned_ready_model(app, email="appearance@example.com"):
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(email=email, username="A")
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
        paper = Paper(title="P", slug=f"ap-{uuid.uuid4().hex[:6]}", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        mid = str(uuid.uuid4())
        model = Model3D(
            id=mid,
            paper_id=paper.id,
            user_id=user.id,
            glb_path=f"converted/{mid}/model.glb",
            processing_status="ready",
            license_type="academic",
            source_format="stl",
            appearance_color="#cccccc",
            access_starts_at=datetime.now(UTC),
            access_expires_at=datetime.now(UTC) + timedelta(days=365),
        )
        db.session.add(model)
        db.session.commit()
        return mid, paper.slug


def test_model_asset_token_changes_on_recolor(app):
    from app import model_asset_token

    with app.app_context():
        m = Model3D(id=str(uuid.uuid4()), paper_id=1, user_id=1, glb_path="x", appearance_color="#aaaaaa")
        before = model_asset_token(m)
        m.appearance_color = "#ff0000"
        after = model_asset_token(m)
    assert before != after


def test_viewer_glb_has_cache_bust_token(client, app):
    mid, _ = _owned_ready_model(app)
    html = client.get(f"/view/{mid}").get_data(as_text=True)
    assert "model.glb?v=" in html


def test_color_save_redirects_to_safe_next(client, app):
    from tests.conftest import login

    mid, slug = _owned_ready_model(app)
    login(client, email="appearance@example.com", password="password123")
    resp = client.post(
        f"/models/{mid}/appearance",
        data={"color": "#112233", "next": f"/papers/{slug}"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert f"/papers/{slug}" in resp.headers["Location"]


def test_color_save_ignores_offsite_next(client, app):
    from tests.conftest import login

    mid, _ = _owned_ready_model(app, email="appearance2@example.com")
    login(client, email="appearance2@example.com", password="password123")
    resp = client.post(
        f"/models/{mid}/appearance",
        data={"color": "#112233", "next": "https://evil.example.com/x"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    # Falls back to the model edit page, never the external URL.
    assert "evil.example.com" not in resp.headers["Location"]
    assert f"/models/{mid}/edit" in resp.headers["Location"]
