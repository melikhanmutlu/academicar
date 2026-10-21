import uuid

from models import Model3D, Paper, User, db
from tests.conftest import login, register, upload_file_bytes, valid_ascii_stl_bytes


def _seed_models(app, email, count, **fields):
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        paper = Paper(title=f"Seed {uuid.uuid4().hex[:6]}", slug=f"seed-{uuid.uuid4().hex[:8]}", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        for _ in range(count):
            db.session.add(Model3D(
                id=str(uuid.uuid4()), paper_id=paper.id, user_id=user.id, glb_path="seed.glb",
                processing_status=fields.get("processing_status", "ready"),
                license_type=fields.get("license_type", "free"),
            ))
        db.session.commit()


def _upload(client, app):
    client.post("/projects/new", data={"title": "Next topic"}, follow_redirects=True)
    with app.app_context():
        slug = Paper.query.filter_by(title="Next topic").one().slug
    return client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "next.stl"), "source_unit": "cm", "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    ), slug


def _model_count(app, slug):
    with app.app_context():
        return Model3D.query.join(Paper).filter(Paper.slug == slug).count()


def test_sixth_live_free_model_is_refused(client, app):
    register(client)
    _seed_models(app, "user@example.com", 5)
    response, slug = _upload(client, app)
    assert b"Free access covers up to 5 models" in response.data
    assert _model_count(app, slug) == 0


def test_failed_and_paid_models_do_not_use_free_slots(client, app):
    register(client)
    _seed_models(app, "user@example.com", 4)
    _seed_models(app, "user@example.com", 3, processing_status="failed")
    _seed_models(app, "user@example.com", 3, license_type="academic")
    response, slug = _upload(client, app)
    assert b"Free access covers" not in response.data
    assert _model_count(app, slug) == 1


def test_chunked_upload_preflight_refuses_a_sixth_free_model(client, app):
    register(client)
    _seed_models(app, "user@example.com", 5)
    client.post("/projects/new", data={"title": "Chunked"}, follow_redirects=True)
    with app.app_context():
        slug = Paper.query.filter_by(title="Chunked").one().slug
    response = client.post("/uploads", json={"filename": "a.stl", "size": 1000, "target": {"paper_slug": slug}})
    assert response.status_code == 413
    assert "Free access covers up to 5 models" in response.get_json()["error"]


def test_free_limit_is_not_advertised(client):
    for path in ("/pricing",):
        assert "5 models" not in client.get(path).get_data(as_text=True)


def test_admin_overview_lists_top_uploaders(client, app):
    register(client, email="admin@example.com", username="Admin")
    register_other = [("a@example.com", "Alice", 3), ("b@example.com", "Bob", 7)]
    with app.app_context():
        User.query.filter_by(email="admin@example.com").one().is_admin = True
        for email, name, _ in register_other:
            user = User(email=email, username=name)
            user.set_password("password123")
            db.session.add(user)
        db.session.commit()
    for email, _, count in register_other:
        _seed_models(app, email, count)
    client.get("/auth/logout")
    login(client, email="admin@example.com")
    html = client.get("/admin").get_data(as_text=True)
    assert "Top uploaders" in html
    assert html.index("Bob") < html.index("Alice")
    assert "7 models" in html and "3 models" in html
