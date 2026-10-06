"""Scenes: saved views with their own link and QR, plan-gated, editor-managed."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from models import Model3D, ModelScene, Paper, ProjectCollaborator, User, db
from scenes import MAX_SCENES_PER_MODEL, clean_scene_state
from tests.plan_helpers import set_plan_features
from tests.conftest import create_user, login

PASSWORD = "password123"
SAVE_BUTTON = "data-scene-save-view>Save this view"
LAYERS = [
    {"name": "Bone", "materials": ["m1"], "color": "#eeeeee"},
    {"name": "Muscle", "materials": ["m2"], "color": "#aa0000"},
]
GOOD_STATE = {
    "v": 1,
    "labels": False,
    "layers": {"Bone": {"visible": False, "opacity": 0.5}, "Muscle": {"visible": True, "opacity": 1}},
    "camera": {"orbit": "10deg 75deg 105%", "target": "0m 0.1m 0m", "fov": "28deg"},
    "background": "white",
}


def _make_model(app, *, plan="academic", visibility="public", email="owner@example.com", layers=True, expires=None):
    with app.app_context():
        user = User.query.filter_by(email=email).first() or create_user(email=email, username="Owner")
        paper = Paper(
            title="Heart", slug=f"p-{uuid.uuid4().hex[:6]}", user_id=user.id,
            is_public=visibility == "public", visibility=visibility,
        )
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id=str(uuid.uuid4()), paper_id=paper.id, user_id=user.id, glb_path="x.glb",
            processing_status="ready", license_type=plan, source_format="glb", file_size=10,
            original_filename="heart.glb", public_id=uuid.uuid4().hex[:20],
            access_starts_at=datetime.now(UTC),
            access_expires_at=expires or datetime.now(UTC) + timedelta(days=365),
            layer_info={"layers": LAYERS, "notes": []} if layers else None,
        )
        db.session.add(model)
        db.session.commit()
        return model.id, paper.id


def _login(client, email="owner@example.com"):
    login(client, email=email, password=PASSWORD)


def _create(client, mid, title="Overview", state=None, **extra):
    return client.post(f"/models/{mid}/scenes", json={"title": title, "state": GOOD_STATE if state is None else state, **extra})


def test_create_update_reorder_delete(app, client):
    mid, _ = _make_model(app)
    _login(client)
    response = _create(client, mid, "Overview", description="Whole heart")
    assert response.status_code == 201
    scene = response.get_json()["scene"]
    assert scene["title"] == "Overview" and scene["description"] == "Whole heart"
    assert scene["url"].endswith(f"/s/{scene['public_id']}")
    assert scene["stale"] is False
    assert scene["state"]["layers"]["Bone"] == {"visible": False, "opacity": 0.5}
    second = _create(client, mid, "Detail").get_json()["scene"]
    assert (scene["order_index"], second["order_index"]) == (0, 1)

    renamed = client.post(f"/models/{mid}/scenes/{scene['id']}", json={"title": "  Heart   overview ", "description": ""})
    assert renamed.get_json()["scene"]["title"] == "Heart overview"
    assert renamed.get_json()["scene"]["description"] == ""
    resaved = client.post(f"/models/{mid}/scenes/{scene['id']}", json={"state": {"v": 1, "labels": True}})
    assert resaved.get_json()["scene"]["state"] == {"v": 1, "labels": True}

    reordered = client.post(f"/models/{mid}/scenes/reorder", json={"order": [second["id"], scene["id"]]})
    assert [s["id"] for s in reordered.get_json()["scenes"]] == [second["id"], scene["id"]]
    assert client.post(f"/models/{mid}/scenes/reorder", json={"order": [second["id"]]}).status_code == 400
    assert client.post(f"/models/{mid}/scenes/reorder", json={"order": [second["id"], 999]}).status_code == 400

    assert client.post(f"/models/{mid}/scenes/{scene['id']}/delete").status_code == 200
    with app.app_context():
        assert [s.id for s in db.session.get(Model3D, mid).scenes] == [second["id"]]
    assert client.post(f"/models/{mid}/scenes/{scene['id']}/delete").status_code == 404


def test_validation_errors(app, client):
    mid, _ = _make_model(app)
    _login(client)
    assert _create(client, mid, "   ").status_code == 400
    assert _create(client, mid, "x" * 121).status_code == 400
    assert _create(client, mid, "ok", description="d" * 501).status_code == 400
    assert client.post(f"/models/{mid}/scenes", data="nope", content_type="text/plain").status_code == 400
    assert client.post(f"/models/{mid}/scenes", json={"title": "No state"}).status_code == 400
    assert _create(client, mid, "Big", state={"v": 1, "pad": "x" * 20000}).status_code == 400
    assert client.post(f"/models/{mid}/scenes/12345", json={"title": "x"}).status_code == 404


def test_anonymous_cannot_create(app, client):
    mid, _ = _make_model(app)
    assert _create(client, mid).status_code in (302, 401)


def test_stranger_forbidden_and_editor_allowed(app, client):
    mid, paper_id = _make_model(app)
    with app.app_context():
        create_user(email="stranger@example.com", username="Stranger")
        editor = create_user(email="editor@example.com", username="Editor")
        db.session.add(ProjectCollaborator(paper_id=paper_id, user_id=editor.id, email=editor.email, accepted_at=datetime.now(UTC)))
        db.session.commit()
    stranger = app.test_client()
    _login(stranger, "stranger@example.com")
    assert _create(stranger, mid).status_code == 403
    _login(client, "editor@example.com")
    created = _create(client, mid)
    assert created.status_code == 201
    scene_id = created.get_json()["scene"]["id"]
    assert client.get(f"/qr-print/scene/{scene_id}/label.png").status_code == 200
    assert stranger.post(f"/models/{mid}/scenes/{scene_id}/delete").status_code == 403
    assert stranger.get(f"/qr-print/scene/{scene_id}/label.png").status_code == 403


def test_plan_without_scenes_is_refused(app, client):
    set_plan_features(app, "free", remove={"scenes"})  # Free has scenes by default; an admin can turn them off
    mid, _ = _make_model(app, plan="free")
    _login(client)
    response = _create(client, mid)
    assert response.status_code == 403
    assert "not included" in response.get_json()["error"].lower()
    with app.app_context():
        assert ModelScene.query.count() == 0


def test_scene_limit(app, client):
    mid, _ = _make_model(app)
    _login(client)
    for i in range(MAX_SCENES_PER_MODEL):
        assert _create(client, mid, f"Scene {i}").status_code == 201
    response = _create(client, mid, "One too many")
    assert response.status_code == 400
    assert str(MAX_SCENES_PER_MODEL) in response.get_json()["error"]


def test_clean_scene_state(app):
    mid, _ = _make_model(app)
    with app.app_context():
        model = db.session.get(Model3D, mid)
        cleaned = clean_scene_state(
            {
                "v": 9, "evil": "<script>", "labels": "yes",
                "layers": {
                    "Bone": {"visible": 0, "opacity": 7},
                    "Muscle": {"visible": True, "opacity": -3},
                    "Ghost": {"visible": True, "opacity": 1},
                    "Bad": "x",
                },
                "camera": {"orbit": "1deg 2deg 3m", "target": "javascript:alert(1)", "fov": "28deg; x"},
                "background": "red",
                "section": {"axis": "y", "offset": 1e9, "flip": True},
            },
            model,
        )
        assert cleaned == {
            "v": 1,
            "layers": {"Bone": {"visible": True, "opacity": 1.0}, "Muscle": {"visible": True, "opacity": 0.0}},
            "camera": {"orbit": "1deg 2deg 3m"},
            "section": {"axis": "y", "offset": 1000.0, "flip": True},
        }
        assert clean_scene_state({"layers": {"Bone": {"visible": False, "opacity": 0.456}}}, model)["layers"]["Bone"] == {"visible": False, "opacity": 0.46}
        assert clean_scene_state({"camera": {"orbit": "0deg 75deg auto", "target": "auto auto auto", "fov": "1.5e-7deg"}}, model)["camera"]["fov"] == "1.5e-7deg"
        assert "camera" not in clean_scene_state({"camera": {"target": "0m 0m 0m"}}, model)
        # Real values from model-viewer carry full float precision.
        browser_camera = {
            "orbit": "1.0409773026968097rad 1.2217304763960306rad -0.05943760950109632m",
            "target": "0.009999999776482582m 0.02250015172637546m -0.009999999667410024m",
            "fov": "27.999999999999996deg",
        }
        assert clean_scene_state({"camera": browser_camera}, model)["camera"] == browser_camera
        assert "section" not in clean_scene_state({"section": {"axis": "w"}}, model)
        for bad in ("text", [], None, {"pad": "x" * 20000}):
            with pytest.raises(ValueError):
                clean_scene_state(bad, model)


def _scene_public_id(app, mid, title="Overview"):
    with app.app_context():
        scene = ModelScene(model_id=mid, public_id=uuid.uuid4().hex, title=title, state={"v": 1}, order_index=0)
        db.session.add(scene)
        db.session.commit()
        return scene.id, scene.public_id


def test_scene_link_redirects_to_viewer_with_scene(app, client):
    mid, _ = _make_model(app)
    scene_id, public_id = _scene_public_id(app, mid)
    response = client.get(f"/s/{public_id}")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/view/{mid}?scene={scene_id}")


def test_scene_link_unknown_and_deleted_scene_404(app, client):
    mid, _ = _make_model(app)
    scene_id, public_id = _scene_public_id(app, mid)
    assert client.get("/s/nope").status_code == 404
    with app.app_context():
        db.session.delete(db.session.get(ModelScene, scene_id))
        db.session.commit()
    assert client.get(f"/s/{public_id}").status_code == 404


def test_scene_link_private_project(app, client):
    mid, _ = _make_model(app, visibility="private")
    _, public_id = _scene_public_id(app, mid)
    assert client.get(f"/s/{public_id}").status_code == 403


def test_scene_link_expired_model_410(app, client):
    mid, _ = _make_model(app, expires=datetime.now(UTC) - timedelta(days=2))
    _, public_id = _scene_public_id(app, mid)
    assert client.get(f"/s/{public_id}").status_code == 410


def test_scene_link_falls_back_when_plan_lost_scenes(app, client):
    set_plan_features(app, "free", remove={"scenes"})
    mid, _ = _make_model(app, plan="free")
    _, public_id = _scene_public_id(app, mid)
    response = client.get(f"/s/{public_id}")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/view/{mid}")


def test_stale_flag_after_layers_change(app, client):
    mid, _ = _make_model(app)
    _login(client)
    scene = _create(client, mid).get_json()["scene"]
    with app.app_context():
        model = db.session.get(Model3D, mid)
        model.layer_info = {"layers": [{"name": "Bone", "materials": ["m1"]}, {"name": "Skin", "materials": ["m3"]}]}
        db.session.commit()
    resaved = client.post(f"/models/{mid}/scenes/{scene['id']}", json={"title": "Overview"}).get_json()["scene"]
    assert resaved["stale"] is True
    with app.test_request_context():
        from scenes import scenes_for_viewer

        assert scenes_for_viewer(db.session.get(Model3D, mid))[0]["stale"] is True


def test_qr_asset_download(app, client):
    mid, _ = _make_model(app)
    scene_id, public_id = _scene_public_id(app, mid, "Heart view")
    assert client.get(f"/qr-print/scene/{scene_id}/label.png").status_code in (302, 401)
    _login(client)
    for asset, mimetype in (("qr.svg", "image/svg+xml"), ("qr-print.png", "image/png"), ("label.png", "image/png")):
        response = client.get(f"/qr-print/scene/{scene_id}/{asset}")
        assert response.status_code == 200 and response.mimetype == mimetype
        assert f"qr-scene-{public_id}-{asset}" in response.headers["Content-Disposition"]
    assert client.get(f"/qr-print/scene/{scene_id}/other.png").status_code == 404
    assert client.get("/qr-print/scene/9999/label.png").status_code == 404


def test_model_deletion_cascades_scenes(app, client):
    mid, _ = _make_model(app)
    _login(client)
    _create(client, mid)
    _create(client, mid, "Two")
    client.post(f"/models/{mid}/delete")
    with app.app_context():
        assert db.session.get(Model3D, mid) is None
        assert ModelScene.query.count() == 0


def test_viewer_shows_scenes_panel_only_when_the_plan_has_scenes(app, client):
    mid, _ = _make_model(app, plan="academic")
    _scene_public_id(app, mid, "Heart view")
    html = client.get(f"/view/{mid}").get_data(as_text=True)
    assert 'id="scenesPanel"' in html and 'id="scenesBtn"' in html
    assert "Heart view" in html
    assert SAVE_BUTTON not in html  # readers cannot save

    _login(client)
    owner_html = client.get(f"/view/{mid}").get_data(as_text=True)
    assert SAVE_BUTTON in owner_html and 'id="tourBar"' in owner_html
    embed_html = client.get(f"/view/{mid}?embed=true").get_data(as_text=True)
    assert 'id="scenesPanel"' in embed_html and SAVE_BUTTON not in embed_html

    set_plan_features(app, "free", remove={"scenes"})
    free_id, _ = _make_model(app, plan="free", email="free@example.com")
    _scene_public_id(app, free_id, "Hidden scene")
    free_client = app.test_client()
    _login(free_client, "free@example.com")
    free_html = free_client.get(f"/view/{free_id}").get_data(as_text=True)
    assert 'id="scenesPanel"' not in free_html and "Hidden scene" not in free_html


def test_viewer_only_activates_scene_of_this_model(app, client):
    mid, _ = _make_model(app)
    other_id, _ = _make_model(app, email="other@example.com")
    own_scene, _ = _scene_public_id(app, mid)
    foreign_scene, _ = _scene_public_id(app, other_id)
    assert f"let activeSceneId = {own_scene};" in client.get(f"/view/{mid}?scene={own_scene}").get_data(as_text=True)
    assert "let activeSceneId = null;" in client.get(f"/view/{mid}?scene={foreign_scene}").get_data(as_text=True)
    assert "let activeSceneId = null;" in client.get(f"/view/{mid}?scene=abc").get_data(as_text=True)


def test_model_edit_scenes_section(app, client):
    mid, _ = _make_model(app)
    scene_id, _ = _scene_public_id(app, mid, "Heart view")
    _login(client)
    html = client.get(f"/models/{mid}/edit").get_data(as_text=True)
    assert 'id="scenesSection"' in html and "Heart view" in html
    assert f"/qr-print/scene/{scene_id}/label.png" in html

    free_id, _ = _make_model(app, plan="free", email="free@example.com")
    free_client = app.test_client()
    _login(free_client, "free@example.com")
    free_html = free_client.get(f"/models/{free_id}/edit").get_data(as_text=True)
    assert "available on paid plans" in free_html
