"""PowerPoint-ready GLB download and the offline presenter mode (presentation.py)."""
import os
import uuid
from datetime import UTC, datetime, timedelta

import trimesh

from converters.glb_optimize import glb_has_draco, optimize_glb
from converters.powerpoint_export import build_powerpoint_glb, count_triangles
from models import ConversionJob, Model3D, Paper, ProjectAttachment, User, db
from presentation import clean_model_slides
from tests.conftest import create_user, login

PASSWORD = "password123"


def _layered_glb(path, subdivisions=4):
    scene = trimesh.Scene()
    for i, name in enumerate(["bone", "skin"]):
        mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=1 + i)
        mesh.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(name=name, baseColorFactor=[200, 100, 50, 255])
        )
        scene.add_geometry(mesh, node_name=name, geom_name=name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    scene.export(path)
    return path


def _make_model(app, *, email="owner@example.com", hidden_layer=False, with_glb=True, expires=None):
    with app.app_context():
        user = User.query.filter_by(email=email).first() or create_user(email=email, username="Owner")
        paper = Paper(title="Heart", slug=f"p-{uuid.uuid4().hex[:6]}", user_id=user.id, is_public=True, visibility="public")
        db.session.add(paper)
        db.session.flush()
        model_id = str(uuid.uuid4())
        glb = os.path.join(app.config["CONVERTED_FOLDER"], model_id, "model.glb")
        if with_glb:
            _layered_glb(glb)
            optimize_glb(glb, keep_layers=True)
        layers = [
            {"name": "Bone", "materials": ["bone"], "color": "#eeeeee"},
            {"name": "Skin", "materials": ["skin"], "color": "#aa0000", **({"visible": False} if hidden_layer else {})},
        ]
        model = Model3D(
            id=model_id, paper_id=paper.id, user_id=user.id, glb_path=glb,
            processing_status="ready", license_type="academic", source_format="glb", file_size=10,
            original_filename="heart.glb", display_name="Heart", public_id=uuid.uuid4().hex[:20],
            access_starts_at=datetime.now(UTC),
            access_expires_at=expires or datetime.now(UTC) + timedelta(days=365),
            layer_info={"layers": layers, "notes": []},
        )
        db.session.add(model)
        db.session.commit()
        return model_id, paper.id, paper.slug


def _login(client, email="owner@example.com"):
    login(client, email=email, password=PASSWORD)


# --- converter ------------------------------------------------------------

def test_powerpoint_glb_is_draco_free_applies_default_view_and_simplifies(tmp_path):
    src = _layered_glb(str(tmp_path / "model.glb"), subdivisions=5)
    assert optimize_glb(src, keep_layers=True) and glb_has_draco(src)
    out = str(tmp_path / "powerpoint.glb")

    assert build_powerpoint_glb(src, out, {"skin"}, {}, max_triangles=5000)
    assert not glb_has_draco(out)
    assert 0 < count_triangles(out) <= 5000
    from pygltflib import GLTF2

    assert [m.name for m in GLTF2.load(out).materials] == ["bone"]


def test_powerpoint_glb_keeps_full_detail_under_the_limit(tmp_path):
    src = _layered_glb(str(tmp_path / "model.glb"))
    out = str(tmp_path / "powerpoint.glb")
    assert build_powerpoint_glb(src, out, max_triangles=10**6)
    assert count_triangles(out) == count_triangles(src)


def test_powerpoint_glb_missing_source_returns_false(tmp_path):
    assert build_powerpoint_glb(str(tmp_path / "nope.glb"), str(tmp_path / "out.glb")) is False


# --- PowerPoint download flow ---------------------------------------------

def test_prepare_builds_and_downloads(app, client):
    mid, _, _ = _make_model(app, hidden_layer=True)
    _login(client)
    assert client.get(f"/models/{mid}/powerpoint").get_json()["status"] == "missing"

    state = client.post(f"/models/{mid}/powerpoint").get_json()  # tests run jobs inline
    assert state["status"] == "ready"
    response = client.get(state["download_url"])
    assert response.status_code == 200
    assert "Heart (PowerPoint).glb" in response.headers["Content-Disposition"]
    assert response.data[:4] == b"glTF"
    with app.app_context():
        job = ConversionJob.query.filter_by(model_id=mid, job_type="powerpoint_glb").one()
        assert job.status == "completed"


def test_file_goes_stale_when_the_default_view_changes(app, client):
    mid, _, _ = _make_model(app)
    _login(client)
    assert client.post(f"/models/{mid}/powerpoint").get_json()["status"] == "ready"
    with app.app_context():
        model = db.session.get(Model3D, mid)
        info = dict(model.layer_info)
        info["layers"] = [dict(info["layers"][0]), {**info["layers"][1], "visible": False}]
        model.layer_info = info
        db.session.commit()
    assert client.get(f"/models/{mid}/powerpoint").get_json()["status"] == "missing"
    assert client.get(f"/models/{mid}/powerpoint.glb").status_code == 404


def test_failed_build_is_reported_and_can_be_retried(app, client):
    mid, _, _ = _make_model(app, with_glb=False)
    _login(client)
    state = client.post(f"/models/{mid}/powerpoint").get_json()
    assert state["status"] == "failed"
    with app.app_context():
        assert db.session.get(Model3D, mid).processing_status == "ready"  # optional job: model untouched
    client.post(f"/models/{mid}/powerpoint")
    with app.app_context():
        assert ConversionJob.query.filter_by(model_id=mid, job_type="powerpoint_glb").count() == 2


def test_powerpoint_needs_an_editor_and_an_active_model(app, client):
    mid, _, _ = _make_model(app)
    assert client.post(f"/models/{mid}/powerpoint").status_code in (302, 401)
    with app.app_context():
        create_user(email="stranger@example.com", username="Stranger")
    _login(client, "stranger@example.com")
    assert client.post(f"/models/{mid}/powerpoint").status_code == 403

    expired, _, _ = _make_model(app, email="owner@example.com", expires=datetime.now(UTC) - timedelta(days=1))
    client.post("/auth/logout")
    _login(client)
    assert client.post(f"/models/{expired}/powerpoint").status_code == 409


def test_project_page_shows_the_powerpoint_button(app, client):
    mid, _, slug = _make_model(app)
    _login(client)
    html = client.get(f"/projects/{slug}").get_data(as_text=True)
    assert f"/models/{mid}/powerpoint" in html


# --- presenter mode -------------------------------------------------------

def _attachment(app, paper_id, slides=None):
    with app.app_context():
        attachment = ProjectAttachment(
            project_id=paper_id, original_filename="talk.pptx", file_type="pptx",
            source_path="talk.pptx", preview_pdf_path="talk.pdf", preview_status="ready", model_slides=slides,
        )
        db.session.add(attachment)
        db.session.commit()
        return attachment.id


def test_clean_model_slides():
    ids = {"a", "b"}
    raw = [
        {"after": 3, "model_id": "b"}, {"after": 0, "model_id": "a"}, {"after": 9, "model_id": "a"},
        {"after": 1, "model_id": "zzz"}, {"after": True, "model_id": "a"}, {"after": -1, "model_id": "a"}, "junk",
    ]
    assert clean_model_slides(raw, 5, ids) == [{"after": 0, "model_id": "a"}, {"after": 3, "model_id": "b"}]
    assert clean_model_slides(raw, None, ids)[-1] == {"after": 9, "model_id": "a"}
    assert clean_model_slides("nope", 5, ids) == []


def test_presenter_page_and_saving_slides(app, client):
    mid, paper_id, slug = _make_model(app)
    aid = _attachment(app, paper_id)
    _login(client)

    html = client.get(f"/p/{slug}/materials/{aid}/present").get_data(as_text=True)
    assert f"/p/{slug}/materials/{aid}/preview" in html
    assert f"/files/{mid}/model.glb" in html
    assert "present-sw.js" in html

    saved = client.post(
        f"/p/{slug}/materials/{aid}/present/slides",
        json={"page_count": 4, "slides": [{"after": 2, "model_id": mid}, {"after": 7, "model_id": mid}]},
    ).get_json()
    assert saved == {"ok": True, "slides": [{"after": 2, "model_id": mid}]}
    with app.app_context():
        assert db.session.get(ProjectAttachment, aid).model_slides == [{"after": 2, "model_id": mid}]

    reader = client.get(f"/p/{slug}/materials/{aid}").get_data(as_text=True)
    assert f"/p/{slug}/materials/{aid}/present" in reader


def test_presenter_is_for_project_editors_only(app, client):
    _, paper_id, slug = _make_model(app)
    aid = _attachment(app, paper_id)
    assert client.get(f"/p/{slug}/materials/{aid}/present").status_code in (302, 401)
    with app.app_context():
        create_user(email="stranger@example.com", username="Stranger")
    _login(client, "stranger@example.com")
    assert client.get(f"/p/{slug}/materials/{aid}/present").status_code == 403
    assert client.post(f"/p/{slug}/materials/{aid}/present/slides", json={"slides": []}).status_code == 403
    reader = client.get(f"/p/{slug}/materials/{aid}").get_data(as_text=True)
    assert "/present\"" not in reader


def test_service_worker_is_served_from_the_root(client):
    response = client.get("/present-sw.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["Content-Type"]
