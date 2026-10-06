"""Layers: finish presets and the saved default view, baked into the GLB and the AR files."""
import os
from pathlib import Path

import pytest
from pygltflib import GLTF2

import app as app_module
from layer_editor import default_ar_materials
from models import ConversionJob, Model3D, db
from tests.conftest import login
from tests.test_layer_editor import PASSWORD, _form, _layers, _make_model


@pytest.fixture()
def usdz(monkeypatch):
    calls = []

    def fake(glb_path, usdz_path):
        calls.append(GLTF2.load(glb_path))
        Path(usdz_path).write_bytes(b"PK-usdz")
        return True

    monkeypatch.setattr(app_module, "convert_glb_to_usdz", fake)
    return calls


def _materials(gltf):
    return {gltf.materials[p.material].name for mesh in gltf.meshes for p in mesh.primitives}


def test_finish_is_baked_into_the_layer_materials(client, app, usdz):
    mid = _make_model(app)
    login(client, email="owner@example.com", password=PASSWORD)
    layers = _layers(app, mid)
    form = _form(layers) | {"layer_metallic_1": "1", "layer_roughness_1": "0.28"}
    response = client.post(f"/models/{mid}/layers", data=form, headers={"Accept": "application/json"})
    assert response.get_json()["ok"], response.get_json()
    with app.app_context():
        model = db.session.get(Model3D, mid)
        gltf = GLTF2.load(model.glb_path)
        stored = model.layer_info["layers"][1]
    material = next(m for m in gltf.materials if m.name in layers[1]["materials"])
    assert material.pbrMetallicRoughness.metallicFactor == 1.0
    assert material.pbrMetallicRoughness.roughnessFactor == pytest.approx(0.28)
    assert (stored["metallic"], stored["roughness"]) == (1.0, 0.28)


def test_default_view_hides_layers_in_the_viewer_and_in_ar(client, app, usdz):
    mid = _make_model(app)
    login(client, email="owner@example.com", password=PASSWORD)
    layers = _layers(app, mid)
    form = _form(layers) | {"layer_visible_0": "0", "layer_visible_1": "1", "layer_opacity_2": "0.4"}
    assert client.post(f"/models/{mid}/layers", data=form, headers={"Accept": "application/json"}).get_json()["ok"]
    with app.app_context():
        model = db.session.get(Model3D, mid)
        assert model.layer_info["layers"][0]["visible"] is False
        assert model.layer_info["layers"][2]["opacity"] == 0.4
        assert default_ar_materials(model) == (set(layers[0]["materials"]), {m: 0.4 for m in layers[2]["materials"]})
        assert ConversionJob.query.filter_by(job_type="usdz_regen", model_id=mid).count() == 1
        ar_glb = Path(app.config["CONVERTED_FOLDER"]) / mid / "ar.glb"
        assert ar_glb.exists()
    # Android's ar.glb drops the hidden layer and keeps the 40% layer faded; the USDZ is built from it.
    from converters.glb_optimize import decompress_glb, glb_has_draco
    plain = ar_glb.parent / "ar-plain.glb"
    if glb_has_draco(str(ar_glb)):
        assert decompress_glb(str(ar_glb), str(plain))
    else:
        plain = ar_glb
    assert _materials(GLTF2.load(str(plain))) == set(layers[1]["materials"]) | set(layers[2]["materials"])
    assert len(usdz) >= 1
    html = client.get(f"/view/{mid}").get_data(as_text=True)
    assert f"/files/{mid}/ar.glb" in html
    assert client.get(f"/files/{mid}/ar.glb").status_code == 200

    # Showing every layer again removes the default-view AR file.
    form = _form(layers) | {f"layer_visible_{i}": "1" for i in range(3)} | {"layer_opacity_2": "1"}
    assert client.post(f"/models/{mid}/layers", data=form, headers={"Accept": "application/json"}).get_json()["ok"]
    assert not ar_glb.exists()
    assert "/ar.glb" not in client.get(f"/view/{mid}").get_data(as_text=True)


def test_hiding_every_layer_is_refused(client, app, usdz):
    mid = _make_model(app)
    login(client, email="owner@example.com", password=PASSWORD)
    layers = _layers(app, mid)
    form = _form(layers) | {f"layer_visible_{i}": "0" for i in range(3)}
    response = client.post(f"/models/{mid}/layers", data=form, headers={"Accept": "application/json"})
    assert response.status_code == 400 and "at least one layer" in response.get_json()["message"]
