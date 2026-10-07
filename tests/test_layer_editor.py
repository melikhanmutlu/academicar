"""Layer editor: owners/editors rename and recolour viewer layers, and whole-model
recolouring is refused for layered models."""
import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import trimesh
from pygltflib import GLTF2

from converters.glb_optimize import glb_has_draco, optimize_glb
from converters.layers import normalize_layers
from converters.stl_converter import _srgb_to_linear
from models import ConversionJob, Model3D, Paper, ProjectCollaborator, User, db
from tests.conftest import create_user, login

PASSWORD = "password123"


def _layered_glb(path, names=("Label 1", "Label 2", "Label 3")):
    scene = trimesh.Scene()
    for i, name in enumerate(names):
        mesh = trimesh.creation.box(extents=(1, 1, 1))
        mesh.apply_translation((i * 2, 0, 0))
        mesh.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(name="m", baseColorFactor=[0.5, 0.5, 0.5, 1.0])
        )
        scene.add_geometry(mesh, node_name=name, geom_name=f"g{i}")
    scene.export(str(path))
    return normalize_layers(str(path))


def _make_model(app, *, layered=True, email="owner@example.com", notes=None, draco=False):
    with app.app_context():
        user = User.query.filter_by(email=email).first() or create_user(email=email, username="Owner")
        paper = Paper(title="P", slug=f"p-{uuid.uuid4().hex[:6]}", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        mid = str(uuid.uuid4())
        folder = Path(app.config["CONVERTED_FOLDER"]) / mid
        folder.mkdir(parents=True)
        glb = folder / "model.glb"
        if layered:
            layers = _layered_glb(glb)
        else:
            layers = []
            trimesh.creation.box().export(str(glb))
        if draco:
            assert optimize_glb(str(glb), keep_layers=True)
            assert glb_has_draco(str(glb))
        info = None
        if layered:
            info = {"layers": layers, "notes": notes or []}
        elif notes:
            info = {"layers": [], "notes": notes}
        model = Model3D(
            id=mid, paper_id=paper.id, user_id=user.id, glb_path=str(glb), processing_status="ready",
            license_type="academic", source_format="segmentation" if layered else "glb",
            appearance_color="#cccccc", original_filename="seg.nii.gz", file_size=glb.stat().st_size,
            access_starts_at=datetime.now(UTC), access_expires_at=datetime.now(UTC) + timedelta(days=365),
            layer_info=info,
        )
        db.session.add(model)
        db.session.commit()
        return mid


def _form(layers, names=None, colors=None):
    data = {}
    for i, layer in enumerate(layers):
        data[f"layer_name_{i}"] = (names or {}).get(i, layer["name"])
        if layer.get("color"):
            data[f"layer_color_{i}"] = (colors or {}).get(i, layer["color"])
    return data


def _layers(app, mid):
    with app.app_context():
        return [dict(l) for l in db.session.get(Model3D, mid).layer_info["layers"]]


def _material_factor(app, mid, name):
    with app.app_context():
        gltf = GLTF2.load(db.session.get(Model3D, mid).glb_path)
    return next(m.pbrMetallicRoughness.baseColorFactor for m in gltf.materials if m.name == name)


@pytest.fixture()
def owner(client, app):
    mid = _make_model(app)
    login(client, email="owner@example.com", password=PASSWORD)
    return mid


def test_rename_persists_without_touching_materials_or_glb(client, app, owner):
    layers = _layers(app, owner)
    with app.app_context():
        glb_path = db.session.get(Model3D, owner).glb_path
    before = Path(glb_path).read_bytes()

    resp = client.post(f"/models/{owner}/layers", data=_form(layers, names={0: "Liver", 1: " Spleen "}))

    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/models/{owner}/edit")
    after = _layers(app, owner)
    assert [l["name"] for l in after] == ["Liver", "Spleen", "Label 3"]
    assert [l["materials"] for l in after] == [l["materials"] for l in layers]  # the viewer finds materials by these
    assert Path(glb_path).read_bytes() == before
    with app.app_context():
        assert db.session.get(Model3D, owner).layer_info["notes"] == []
    assert b"Liver" in client.get(f"/view/{owner}").data
    page = client.get(f"/models/{owner}/edit").data
    assert b'value="Liver"' in page and b'value="Spleen"' in page


def test_colour_change_rewrites_material_in_linear_space_and_layer_info(client, app, owner):
    layers = _layers(app, owner)
    other_before = _material_factor(app, owner, layers[2]["materials"][0])

    resp = client.post(f"/models/{owner}/layers", data=_form(layers, colors={1: "#336699"}))

    assert resp.status_code == 302
    expected = [_srgb_to_linear(c / 255.0) for c in (0x33, 0x66, 0x99)]
    assert _material_factor(app, owner, layers[1]["materials"][0])[:3] == pytest.approx(expected, abs=1e-4)
    assert _material_factor(app, owner, layers[2]["materials"][0]) == other_before
    after = _layers(app, owner)
    assert after[1]["color"] == "#336699"
    assert after[0]["color"] == layers[0]["color"]
    with app.app_context():
        model = db.session.get(Model3D, owner)
        assert model.file_size == os.path.getsize(model.glb_path)
        # the iOS companion is rebuilt from the recoloured GLB
        assert ConversionJob.query.filter_by(model_id=owner, job_type="usdz_regen").count() == 1


def test_unchanged_colours_do_not_rewrite_the_glb(client, app, owner):
    layers = _layers(app, owner)
    with app.app_context():
        glb_path = db.session.get(Model3D, owner).glb_path
    before = Path(glb_path).read_bytes()

    client.post(f"/models/{owner}/layers", data=_form(layers, names={0: "Renamed"}))

    assert Path(glb_path).read_bytes() == before
    with app.app_context():
        assert ConversionJob.query.filter_by(model_id=owner).count() == 0


def test_recolouring_a_draco_glb_keeps_geometry_valid(client, app):
    mid = _make_model(app, draco=True)
    login(client, email="owner@example.com", password=PASSWORD)
    layers = _layers(app, mid)

    client.post(f"/models/{mid}/layers", data=_form(layers, colors={0: "#ff0000"}))

    with app.app_context():
        glb_path = db.session.get(Model3D, mid).glb_path
    assert glb_has_draco(glb_path)
    assert _material_factor(app, mid, layers[0]["materials"][0])[:3] == pytest.approx([1.0, 0.0, 0.0], abs=1e-4)
    cli = Path(__file__).parent.parent / "node_modules" / ".bin" / "gltf-transform"
    if cli.exists():
        result = subprocess.run([str(cli), "inspect", glb_path], capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        assert "KHR_draco_mesh_compression" in result.stdout


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(layer_color_0="red"), "hex"),
        (lambda d: d.update(layer_color_0="#12345"), "hex"),
        (lambda d: d.update(layer_name_0=""), "needs a name"),
        (lambda d: d.update(layer_name_0="   "), "needs a name"),
        (lambda d: d.update(layer_name_0="x" * 81), "80"),
        (lambda d: d.update(layer_name_0="Label 2"), "unique"),
        (lambda d: d.update(layer_name_0="label 2"), "unique"),
    ],
)
def test_invalid_input_is_rejected_and_nothing_changes(client, app, owner, mutate, message):
    layers = _layers(app, owner)
    with app.app_context():
        glb_path = db.session.get(Model3D, owner).glb_path
    before = Path(glb_path).read_bytes()
    data = _form(layers)
    mutate(data)

    resp = client.post(f"/models/{owner}/layers", data=data, follow_redirects=True)

    assert resp.status_code == 200
    assert message.encode() in resp.data
    assert _layers(app, owner) == layers
    assert Path(glb_path).read_bytes() == before


def test_name_of_80_characters_is_accepted(client, app, owner):
    layers = _layers(app, owner)
    client.post(f"/models/{owner}/layers", data=_form(layers, names={0: "n" * 80}))
    assert _layers(app, owner)[0]["name"] == "n" * 80


def test_non_editor_gets_403(client, app, owner):
    with app.app_context():
        create_user(email="intruder@example.com", username="Intruder")
    client = app.test_client()
    login(client, email="intruder@example.com", password=PASSWORD)
    layers = _layers(app, owner)

    resp = client.post(f"/models/{owner}/layers", data=_form(layers, names={0: "Hacked"}))

    assert resp.status_code == 403
    assert _layers(app, owner) == layers


def test_anonymous_is_sent_to_login(client, app, owner):
    client = app.test_client()
    resp = client.post(f"/models/{owner}/layers", data=_form(_layers(app, owner)))
    assert resp.status_code in (302, 401) and "/auth/login" in resp.headers.get("Location", "/auth/login")


def test_project_editor_can_edit_layers(client, app, owner):
    with app.app_context():
        editor = create_user(email="editor@example.com", username="Editor")
        paper = db.session.get(Model3D, owner).paper
        db.session.add(ProjectCollaborator(paper_id=paper.id, user_id=editor.id, email=editor.email, accepted_at=datetime.now(UTC)))
        db.session.commit()
    client = app.test_client()
    login(client, email="editor@example.com", password=PASSWORD)
    layers = _layers(app, owner)

    resp = client.post(f"/models/{owner}/layers", data=_form(layers, names={2: "Shared edit"}))

    assert resp.status_code == 302
    assert _layers(app, owner)[2]["name"] == "Shared edit"


def test_model_without_layers_is_404(client, app):
    mid = _make_model(app, layered=False)
    login(client, email="owner@example.com", password=PASSWORD)

    assert client.post(f"/models/{mid}/layers", data={"layer_name_0": "x"}).status_code == 404


def test_unknown_model_is_404(client, app, owner):
    assert client.post(f"/models/{uuid.uuid4()}/layers", data={}).status_code == 404


# --- model_edit page ---------------------------------------------------------


def test_edit_page_shows_layers_card_and_hides_whole_model_colour(client, app, owner):
    page = client.get(f"/models/{owner}/edit").get_data(as_text=True)

    assert 'id="layersSection"' in page
    assert f'action="/models/{owner}/layers"' in page
    assert page.count('name="layer_name_') == 3
    assert "Colours are set per layer" in page
    assert 'id="appearanceColor"' not in page and 'class="color-presets"' not in page
    # the form stays valid and recolour-free without the picker
    assert 'name="color" value="#cccccc"' in page and 'name="color_changed" value="0"' in page


def test_edit_page_without_layers_keeps_colour_picker_and_has_no_layers_card(client, app):
    mid = _make_model(app, layered=False)
    login(client, email="owner@example.com", password=PASSWORD)

    page = client.get(f"/models/{mid}/edit").get_data(as_text=True)

    assert 'id="layersSection"' not in page
    assert 'id="appearanceColor"' in page and "Colours are set per layer" not in page


def test_processing_notes_are_listed_for_editors(client, app):
    notes = ["Downsampled to 0.9 mm voxels", "Only the 64 largest structures are shown"]
    layered = _make_model(app, notes=notes)
    single = _make_model(app, layered=False, notes=["This scan is not CT <b>x</b>"])
    plain = _make_model(app, layered=False)
    login(client, email="owner@example.com", password=PASSWORD)

    for mid in (layered, single):
        page = client.get(f"/models/{mid}/edit").get_data(as_text=True)
        assert "Processing notes" in page
    page = client.get(f"/models/{layered}/edit").get_data(as_text=True)
    assert all(note in page for note in notes)
    single_page = client.get(f"/models/{single}/edit").get_data(as_text=True)
    assert "This scan is not CT &lt;b&gt;x&lt;/b&gt;" in single_page  # escaped
    assert "Processing notes" not in client.get(f"/models/{plain}/edit").get_data(as_text=True)


# --- whole-model recolour is refused for layered models ----------------------


def test_viewer_color_route_refuses_layered_models(client, app, owner, monkeypatch):
    called = []
    monkeypatch.setattr("app._bake_viewer_color", lambda model, color: called.append(color) or (True, "ok"))

    resp = client.post(f"/models/{owner}/viewer-color", json={"color": "#336699"})

    assert resp.status_code == 400 and resp.get_json()["ok"] is False
    assert "per layer" in resp.get_json()["error"]
    assert called == []


def test_appearance_route_refuses_colour_change_on_layered_models(client, app, owner, monkeypatch):
    called = []
    monkeypatch.setattr("app._apply_model_appearance_change", lambda model, form: called.append(1) or (True, "ok", "success", {}))

    for form in ({"color": "#336699", "color_changed": "1"}, {"color": "#336699"}):
        resp = client.post(f"/models/{owner}/appearance", data=form, follow_redirects=True)
        assert b"Colours are set per layer" in resp.data
    assert called == []


def test_appearance_route_still_saves_name_and_finish_on_layered_models(client, app, owner, monkeypatch):
    seen = []
    monkeypatch.setattr("app._apply_model_appearance_change", lambda model, form: seen.append(dict(form)) or (True, "Changes saved.", "success", {}))

    client.post(
        f"/models/{owner}/appearance",
        data={"color": "#cccccc", "color_changed": "0", "finish_changed": "1", "roughness": "0.5", "display_name": "Abdomen"},
    )
    client.post(f"/models/{owner}/appearance", data={"color": "#cccccc", "color_changed": "0", "finish_changed": "0", "display_name": "Abdomen"})

    assert len(seen) == 2


def test_appearance_route_unchanged_for_models_without_layers(client, app, monkeypatch):
    mid = _make_model(app, layered=False)
    login(client, email="owner@example.com", password=PASSWORD)
    seen = []
    monkeypatch.setattr("app._apply_model_appearance_change", lambda model, form: seen.append(1) or (True, "ok", "success", {}))

    client.post(f"/models/{mid}/appearance", data={"color": "#336699", "color_changed": "1"})

    assert seen == [1]


# --- viewer ------------------------------------------------------------------


def test_viewer_colour_tools_paint_every_layer_of_layered_models_and_show_counts(client, app):
    mid = _make_model(app)
    with app.app_context():
        model = db.session.get(Model3D, mid)
        info = dict(model.layer_info)
        info["layers"] = [dict(info["layers"][0], count=12)] + info["layers"][1:]
        model.layer_info = info
        db.session.commit()
        model_id = model.id
    login(client, email="owner@example.com", password=PASSWORD)

    page = client.get(f"/view/{model_id}").get_data(as_text=True)

    assert "Colours every layer. To colour one layer, open Layers" in page
    assert 'data-color-tools data-color-layered' in page and 'data-color-swatch="' in page
    assert '"count": 12' in page or '"count":12' in page


def test_viewer_keeps_colour_tools_for_models_without_layers(client, app):
    mid = _make_model(app, layered=False)
    login(client, email="owner@example.com", password=PASSWORD)

    page = client.get(f"/view/{mid}").get_data(as_text=True)

    assert 'class="viewer-color-tools" data-color-tools' in page and "Colours are set per layer." not in page


def test_viewer_saves_layer_colours_as_json(client, app, owner):
    layers = _layers(app, owner)
    resp = client.post(
        f"/models/{owner}/layers",
        data=_form(layers, colors={0: "#00aaff"}),
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200 and resp.get_json() == {"ok": True, "message": "Layers saved."}
    assert _layers(app, owner)[0]["color"] == "#00aaff"

    bad = client.post(f"/models/{owner}/layers", data=_form(layers, colors={0: "red"}), headers={"Accept": "application/json"})
    assert bad.status_code == 400 and bad.get_json()["ok"] is False and "hex" in bad.get_json()["message"]


def test_viewer_offers_saving_layer_colours_to_editors_only(client, app, owner):
    html = client.get(f"/view/{owner}").get_data(as_text=True)
    assert "data-layers-save data-url" in html and 'type="color" class="layer-swatch"' in html
    client.post("/auth/logout")
    with app.app_context():
        paper = db.session.get(Model3D, owner).paper
        paper.visibility, paper.is_public = "public", True
        db.session.commit()
    visitor = client.get(f"/view/{owner}").get_data(as_text=True)
    assert "data-layers-save data-url" not in visitor  # visitors can preview colours, not save them
    assert 'type="color" class="layer-swatch"' in visitor


def test_colour_change_drops_stale_automatic_media_but_rename_keeps_it(client, app, owner):
    from models import ModelMedia

    with app.app_context():
        for source in ("auto", "user"):
            db.session.add(ModelMedia(model_id=owner, kind="image", source=source,
                                      label=f"{source} view", filename=f"{source}.jpg", mimetype="image/jpeg"))
        db.session.commit()
    layers = _layers(app, owner)
    client.post(f"/models/{owner}/layers", data=_form(layers, names={0: "Renamed"}))
    with app.app_context():
        assert sorted(m.source for m in ModelMedia.query.filter_by(model_id=owner)) == ["auto", "user"]
    client.post(f"/models/{owner}/layers", data=_form(_layers(app, owner), colors={1: "#336699"}))
    with app.app_context():
        assert [m.source for m in ModelMedia.query.filter_by(model_id=owner)] == ["user"]
