"""Scene AR variants also carry a scene's layer colours and section cut."""
import os

import numpy as np
import pytest
import trimesh
from pygltflib import GLTF2

import app as app_module
from converters.scene_variant import build_scene_variant
from models import Model3D, ModelScene, db
from scenes import clean_scene_state, scene_ar_signature
from tests.test_scene_ar import _add_scene, _layered_glb, _make_model, _plain, _used_materials, r2, usdz  # noqa: F401

# Spheres A, B, C (radius 1 cm) at x = 0, 5, 10 cm: the bounding box centre is x = 5 cm.
CUT_X = {"axis": "x", "offset": 0.0, "flip": False}


def _x_extent(path, tmp_path, material):
    plain = tmp_path / "extent.glb"
    from converters.glb_optimize import decompress_glb, glb_has_draco
    if glb_has_draco(str(path)):
        assert decompress_glb(str(path), str(plain))
        path = plain
    scene = trimesh.load(str(path), force="scene")
    xs = []
    for node in scene.graph.nodes_geometry:
        transform, geom_name = scene.graph[node]
        geometry = scene.geometry[geom_name]
        if geometry.visual.material.name == material:
            xs.append(trimesh.transform_points(geometry.vertices, transform)[:, 0])
    xs = np.concatenate(xs)
    return xs.min(), xs.max()


def test_variant_recolours_layers_and_keeps_alpha(tmp_path):
    source = tmp_path / "src.glb"
    _layered_glb(source)
    out = tmp_path / "out.glb"
    assert build_scene_variant(str(source), str(out), set(), {"B": 0.5}, colors={"B": "#ff0000", "C": "#00ff00"})
    materials = {m.name: m for m in _plain(out, tmp_path).materials}
    assert materials["B"].pbrMetallicRoughness.baseColorFactor == pytest.approx([1.0, 0.0, 0.0, 0.5])
    assert materials["C"].pbrMetallicRoughness.baseColorFactor == pytest.approx([0.0, 1.0, 0.0, 1.0])


def test_section_keeps_the_lower_side_and_flip_the_upper(tmp_path):
    source = tmp_path / "src.glb"
    _layered_glb(source)
    lower = tmp_path / "lower.glb"
    assert build_scene_variant(str(source), str(lower), set(), {}, section=CUT_X)
    assert _used_materials(_plain(lower, tmp_path)) == {"A", "B"}
    assert _x_extent(lower, tmp_path, "B")[1] == pytest.approx(0.05, abs=1e-4)
    assert _x_extent(lower, tmp_path, "A") == pytest.approx((-0.01, 0.01), abs=1e-3)
    assert all(m.doubleSided for m in _plain(lower, tmp_path).materials)  # the inside shows, as in the viewer

    upper = tmp_path / "upper.glb"
    assert build_scene_variant(str(source), str(upper), set(), {}, section={**CUT_X, "offset": 0.045, "flip": True})
    assert _used_materials(_plain(upper, tmp_path)) == {"C"}
    assert _x_extent(upper, tmp_path, "C")[0] == pytest.approx(0.095, abs=1e-4)


def test_section_that_leaves_nothing_fails_quietly(tmp_path):
    source = tmp_path / "src.glb"
    _layered_glb(source)
    out = tmp_path / "out.glb"
    assert build_scene_variant(str(source), str(out), {"A", "B"}, {}, section={**CUT_X, "offset": -0.02}) is False
    assert not out.exists()


def test_scene_state_keeps_valid_layer_colours_and_signature_follows_them():
    class Model:
        layer_info = {"layers": [{"name": "A"}, {"name": "B"}]}

    state = clean_scene_state({"layers": {
        "A": {"visible": True, "opacity": 1, "color": "#FF8800"},
        "B": {"visible": True, "opacity": 1, "color": "red"},
    }}, Model())
    assert state["layers"]["A"]["color"] == "#ff8800"
    assert "color" not in state["layers"]["B"]
    assert scene_ar_signature(state) == [["A", True, 1, "#ff8800"]]
    assert scene_ar_signature({"layers": {}, "section": CUT_X}) == [["@section", "x", 0.0, False]]
    # Older scenes (no colour, no section) keep their signature, so they are not rebuilt.
    assert scene_ar_signature({"layers": {"A": {"visible": False, "opacity": 1}}}) == [["A", False, 1]]


def test_job_builds_a_variant_for_a_section_only_scene(app, usdz, r2, tmp_path):
    model_id = _make_model(app)
    _add_scene(app, model_id, {"v": 1, "section": CUT_X, "layers": {"C": {"visible": True, "opacity": 1, "color": "#123456"}}}, ar_status="queued")
    with app.app_context():
        app_module.process_scene_ar_job(app, scene_id=1)
        scene = db.session.get(ModelScene, 1)
        assert scene.ar_status == "ready", scene.ar_status
        assert os.path.exists(scene.ar_glb_path) and scene.ar_usdz_path
        assert db.session.get(Model3D, model_id).processing_status == "ready"
        variant = _plain(scene.ar_glb_path, tmp_path)
    assert _used_materials(variant) == {"A", "B"}  # C is beyond the cut


def test_ar_status_endpoint_reports_the_variant_and_respects_visibility(app, client):
    model_id = _make_model(app)
    _add_scene(app, model_id, ar_status="queued")
    assert client.get(f"/models/{model_id}/scenes/1/ar-status").get_json() == {
        "id": 1, "ar_status": "queued", "ar_glb_url": None, "ar_usdz_url": None,
    }
    with app.app_context():
        scene = db.session.get(ModelScene, 1)
        scene.ar_status, scene.ar_glb_path = "ready", "x.glb"
        db.session.commit()
    data = client.get(f"/models/{model_id}/scenes/1/ar-status").get_json()
    assert data["ar_status"] == "ready" and data["ar_glb_url"].endswith(f"/files/{model_id}/scenes/1.glb")
    assert client.get(f"/models/{model_id}/scenes/99/ar-status").status_code == 404
    private_id = _make_model(app, visibility="private", email="private@example.com")
    _add_scene(app, private_id)
    assert client.get(f"/models/{private_id}/scenes/2/ar-status").status_code == 404
