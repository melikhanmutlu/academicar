"""Regression tests for the v6.3 fourth audit fixes.

Covers: model_edit input length validation, health check endpoint,
unused function removal.
"""
from models import Model3D, Paper, User, db


def test_model_edit_truncates_long_display_name(client):
    """display_name longer than 255 chars should be truncated server-side."""
    from tests.conftest import login, register

    register(client, email="trunc@example.com")
    app = client.application
    with app.app_context():
        user = User.query.filter_by(email="trunc@example.com").one()
        paper = Paper(title="Trunc Paper", slug="trunc-paper", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="trunc-model",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="model.glb",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()

    login(client, email="trunc@example.com")
    long_name = "A" * 300
    resp = client.post(
        "/models/trunc-model/edit",
        data={"display_name": long_name, "description": "short desc"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        model = db.session.get(Model3D, "trunc-model")
        assert model.display_name is not None
        assert len(model.display_name) <= 255


def test_model_edit_truncates_long_description(client):
    """description longer than 5000 chars should be truncated server-side."""
    from tests.conftest import login, register

    register(client, email="desc@example.com")
    app = client.application
    with app.app_context():
        user = User.query.filter_by(email="desc@example.com").one()
        paper = Paper(title="Desc Paper", slug="desc-paper", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="desc-model",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="model.glb",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()

    login(client, email="desc@example.com")
    long_desc = "B" * 6000
    resp = client.post(
        "/models/desc-model/edit",
        data={"display_name": "Name", "description": long_desc},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        model = db.session.get(Model3D, "desc-model")
        assert model.description is not None
        assert len(model.description) <= 5000


def test_health_check_returns_200(client):
    """GET /health should return 200 with status ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
