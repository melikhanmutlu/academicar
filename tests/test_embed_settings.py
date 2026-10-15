"""Embed settings: saved per model, limited by plan, applied by the embedded viewer."""
import json
import os
import uuid

import pytest

from embed_config import clean_embed_config, embed_snippet
from models import Model3D, Paper, User, db
from tests.conftest import register


def _model(app, license_type="academic", email="user@example.com"):
    with app.app_context():
        owner = User.query.filter_by(email=email).first()
        paper = Paper(title="Embed project", slug=f"embed-{uuid.uuid4().hex[:8]}", user_id=owner.id,
                      visibility="public", is_public=True)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(id=str(uuid.uuid4()), paper_id=paper.id, user_id=owner.id, display_name="Femur",
                        glb_path="converted/x/model.glb", license_type=license_type, processing_status="ready")
        folder = os.path.join(app.config["CONVERTED_FOLDER"], model.id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "model.glb"), "wb") as fh:
            fh.write(b"glTF")
        db.session.add(model)
        db.session.commit()
        return model.id


def _save(client, model_id, **changes):
    return client.post(f"/models/{model_id}/embed", json=changes, headers={"X-CSRFToken": "x"})


def _public_embed(client, model_id, query="embed=true"):
    client.post("/auth/logout")
    return client.get(f"/view/{model_id}?{query}")


def test_clean_embed_config_defaults_and_validation():
    cfg = clean_embed_config({})
    assert cfg["layout"] == "compact" and cfg["brand"] is True and cfg["size"]["height"] == 480
    assert cfg["tools"] == ["tour", "measure", "scenes", "layers", "slice", "ar"]
    for bad in ({"layout": "huge"}, {"tools": ["hack"]}, {"theme": "red"}, {"size": {"mode": "x"}},
                {"size": {"height": 5}}, {"size": {"width": 99999}}, {"title": "yes"}, "no"):
        with pytest.raises(ValueError):
            clean_embed_config(bad)


def test_snippet_follows_the_size_mode(client, app):
    register(client)
    model_id = _model(app)
    with app.app_context(), app.test_request_context():
        model = db.session.get(Model3D, model_id)
        fixed = embed_snippet(model, clean_embed_config({"size": {"mode": "fixed", "width": 640, "height": 360}}))
        assert 'width="640"' in fixed and 'height="360"' in fixed
        ratio = embed_snippet(model, clean_embed_config({"size": {"mode": "ratio", "ratio": "4:3"}}))
        assert 'width="100%"' in ratio and "aspect-ratio:4/3" in ratio and "height=" not in ratio
        fill = embed_snippet(model, clean_embed_config({"size": {"mode": "fill"}}))
        assert 'width="100%" height="100%"' in fill
        assert f"/view/{model_id}?embed=true" in fill


def test_saved_settings_shape_the_compact_embed(client, app):
    register(client)
    model_id = _model(app)
    res = _save(client, model_id, tools=["measure"], title=False, theme="light", brand=False)
    assert res.status_code == 200 and res.get_json()["ok"]
    html = _public_embed(client, model_id).get_data(as_text=True)
    assert 'id="measureBtn"' in html
    assert 'id="tourBtn"' not in html and 'id="arBtn"' not in html and 'id="layersBtn"' not in html
    assert "data-embed-title" not in html.split("<body", 1)[1]
    assert 'var m = "light"' in html
    assert "data-embed-nobrand" in html


def test_full_layout_renders_the_whole_viewer_in_the_frame(client, app):
    register(client)
    model_id = _model(app)
    assert _save(client, model_id, layout="full").status_code == 200
    response = _public_embed(client, model_id)
    html = response.get_data(as_text=True)
    assert "X-Frame-Options" not in response.headers
    assert 'id="controlPanel"' in html and 'id="fullscreenBtn"' in html
    assert "Sleek Overlay for Embed" not in html


def test_free_plan_keeps_the_compact_branded_embed(client, app):
    register(client)
    model_id = _model(app, license_type="free")
    assert _save(client, model_id, tools=["measure"]).status_code == 200  # basic settings are open to all plans
    assert _save(client, model_id, layout="full").status_code == 400
    assert _save(client, model_id, brand=False).status_code == 400
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        model.embed_config = {"layout": "full", "brand": False}  # e.g. saved before a downgrade
        db.session.commit()
    html = _public_embed(client, model_id).get_data(as_text=True)
    assert "Sleek Overlay for Embed" in html and "data-embed-nobrand" not in html


def test_plan_without_the_embed_feature_gets_the_default_embed(client, app, monkeypatch):
    import dataclasses

    import licensing

    register(client)
    model_id = _model(app)
    _save(client, model_id, tools=["measure"], title=False)
    academic = licensing.get_license_plan("academic")
    monkeypatch.setitem(
        licensing.LICENSE_PLANS, "academic",
        dataclasses.replace(academic, features=frozenset(academic.features - {"embed"})),
    )
    assert _save(client, model_id, tools=["tour"]).status_code == 403
    html = _public_embed(client, model_id).get_data(as_text=True)
    assert "data-embed-title" in html.split("<body", 1)[1]  # saved settings ignored


def test_owner_previews_unsaved_settings_but_visitors_cannot(client, app):
    register(client)
    model_id = _model(app)
    cfg = json.dumps({"tools": ["tour"], "title": False})
    mine = client.get(f"/view/{model_id}?embed=true&cfg={cfg}").get_data(as_text=True)
    assert "data-embed-title" not in mine.split("<body", 1)[1] and 'id="measureBtn"' not in mine
    visitor = _public_embed(client, model_id, f"embed=true&cfg={cfg}").get_data(as_text=True)
    assert 'id="measureBtn"' in visitor and "data-embed-title" in visitor.split("<body", 1)[1]
    assert client.get(f"/view/{model_id}?embed=true&cfg=not-json").status_code == 200


def test_only_editors_open_the_embed_page(client, app):
    register(client)
    model_id = _model(app)
    page = client.get(f"/models/{model_id}/embed")
    assert page.status_code == 200 and "Embed code" in page.get_data(as_text=True)
    client.post("/auth/logout")
    register(client, email="other@example.com")
    assert client.get(f"/models/{model_id}/embed").status_code in (403, 404)
    assert _save(client, model_id, theme="light").status_code in (403, 404)


def test_embed_page_shows_plan_gated_values_not_stale_saved_ones(client, app):
    register(client)
    model_id = _model(app, license_type="free")
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        model.embed_config = {"layout": "full", "brand": False}  # saved before a downgrade
        db.session.commit()
    html = client.get(f"/models/{model_id}/embed").get_data(as_text=True)
    brand = html.split('data-embed-field="brand-hide"', 1)[1].split(">", 1)[0]
    assert "checked" not in brand
    layout = html.split('data-embed-field="layout"', 1)[1].split("</select>", 1)[0]
    assert 'value="compact" selected' in layout


def test_embed_ignores_the_stored_viewer_background_and_keeps_embed_in_gallery_links(client, app):
    register(client)
    model_id = _model(app)
    html = _public_embed(client, model_id).get_data(as_text=True)
    assert "var saved = null;" in html
    assert "['design', 'mode', 'embed']" in html
