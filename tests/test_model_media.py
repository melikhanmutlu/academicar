"""Saved model media: upload, limits, replace-on-regenerate, access, delete."""
import io
import os

from PIL import Image

import model_media
from models import ModelMedia, db
from tests.test_scenes import _login, _make_model


def _png(color=(200, 50, 50)):
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, "PNG")
    return buffer.getvalue()


MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def _upload(client, mid, data, name="shot.png", mimetype="image/png", **form):
    return client.post(
        f"/models/{mid}/media",
        data={"file": (io.BytesIO(data), name, mimetype), **form},
        content_type="multipart/form-data",
    )


def test_owner_uploads_lists_serves_and_deletes(app, client):
    mid, _ = _make_model(app)
    _login(client)
    response = _upload(client, mid, _png(), label="Front view")
    assert response.status_code == 201, response.get_json()
    item = response.get_json()["media"]
    assert item["kind"] == "image" and item["source"] == "user" and item["label"] == "Front view"
    assert _upload(client, mid, MP4, "rec.mp4", "video/mp4").status_code == 201
    listed = client.get(f"/models/{mid}/media").get_json()["media"]
    assert [m["kind"] for m in listed] == ["video", "image"]  # newest first
    served = client.get(item["download_url"])
    assert served.status_code == 200 and served.data[:4] == b"\x89PNG"
    assert "attachment" in served.headers["Content-Disposition"]
    assert client.post(f"/models/{mid}/media/{item['id']}/delete").get_json()["ok"]
    with app.app_context():
        assert ModelMedia.query.count() == 1
    assert client.get(item["url"]).status_code == 404


def test_bad_files_and_limits_are_refused(app, client, monkeypatch):
    mid, _ = _make_model(app)
    _login(client)
    assert _upload(client, mid, b"not a png").status_code == 400
    assert _upload(client, mid, b"not an mp4 at all", "x.mp4", "video/mp4").status_code == 400
    assert _upload(client, mid, b"text", "x.txt", "text/plain").status_code == 400
    monkeypatch.setattr(model_media, "MAX_IMAGES_PER_MODEL", 2)
    assert _upload(client, mid, _png()).status_code == 201
    assert _upload(client, mid, _png()).status_code == 201
    response = _upload(client, mid, _png())
    assert response.status_code == 400 and "at most 2" in response.get_json()["error"]


def test_auto_media_replaces_the_previous_file_of_the_same_name(app, client):
    mid, _ = _make_model(app)
    _login(client)
    first = _upload(client, mid, _png(), source="auto", label="Front").get_json()["media"]
    second = _upload(client, mid, _png((1, 2, 3)), source="auto", label="Front").get_json()["media"]
    with app.app_context():
        assert [m.id for m in ModelMedia.query.all()] == [second["id"]]
        folder = os.path.join(app.config["CONVERTED_FOLDER"], mid, "media")
        assert len(os.listdir(folder)) == 1
    assert first["id"] != second["id"]


def test_media_is_private_to_project_editors(app, client):
    mid, _ = _make_model(app)
    _login(client)
    url = _upload(client, mid, _png()).get_json()["media"]["url"]
    client.post("/auth/logout")
    assert client.get(url).status_code in (302, 401)
    _make_model(app, email="other@example.com")
    _login(client, "other@example.com")
    assert client.get(url).status_code in (403, 404)
    assert _upload(client, mid, _png()).status_code in (403, 404)


def test_media_shows_in_the_viewer_panel_and_on_the_project_card(app, client):
    mid, _ = _make_model(app)
    _login(client)
    _upload(client, mid, _png(), label="Front view")
    viewer = client.get(f"/view/{mid}").get_data(as_text=True)
    assert "data-media-tools" in viewer and "Front view" in viewer
    assert '"have": []' in viewer or '"have":[]' in viewer  # nothing automatic yet: the browser makes it
    with app.app_context():
        from models import Model3D
        slug = db.session.get(Model3D, mid).paper.slug
    card = client.get(f"/projects/{slug}").get_data(as_text=True)
    assert 'class="model-media"' in card and f"/files/{mid}/media/" in card
    client.post("/auth/logout")
    public = client.get(f"/view/{mid}").get_data(as_text=True)
    assert 'class="viewer-media-tools"' not in public and "Front view" not in public


def test_replacing_the_model_drops_automatic_media_but_keeps_captures(client):
    from models import Model3D, Paper
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    register(client)
    client.post("/papers/new", data={"title": "Media Replace"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Media Replace").one().slug
    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "first.stl"), "compliance_confirm": "yes", "source_unit": "cm"},
        content_type="multipart/form-data",
    )
    with client.application.app_context():
        mid = Model3D.query.one().id
    _upload(client, mid, _png(), source="auto", label="View: Front")
    _upload(client, mid, _png(), label="My capture")
    client.post(
        f"/models/{mid}/replace",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "second.stl"), "compliance_confirm": "yes", "source_unit": "cm"},
        content_type="multipart/form-data",
    )
    with client.application.app_context():
        assert [(m.source, m.label) for m in ModelMedia.query.all()] == [("user", "My capture")]
        assert len(os.listdir(os.path.join(client.application.config["CONVERTED_FOLDER"], mid, "media"))) == 1


def test_account_deletion_removes_mirrored_files(client, monkeypatch):
    import app as app_module
    from models import Model3D, Paper
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    deleted = []
    monkeypatch.setattr(app_module, "mirror_delete", deleted.append)
    monkeypatch.setattr("model_media.mirror_delete", deleted.append, raising=False)
    register(client)
    client.post("/papers/new", data={"title": "Gone"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Gone").one().slug
    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "a.stl"), "compliance_confirm": "yes", "source_unit": "cm"},
        content_type="multipart/form-data",
    )
    with client.application.app_context():
        mid = Model3D.query.one().id
    filename = _upload(client, mid, _png()).get_json()["media"]["url"]
    with client.application.app_context():
        media_key = f"converted/{mid}/media/{ModelMedia.query.one().filename}"
    client.post("/account/delete", data={"confirm": "DELETE", "current_password": "password123"})
    assert f"converted/{mid}/model.glb" in deleted and media_key in deleted and f"converted/{mid}/ar.glb" in deleted
    assert filename
