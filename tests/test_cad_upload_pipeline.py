"""STEP / STP uploads through the real pipeline: upload → job → GLB with viewer layers."""
import json
import os
import struct
from pathlib import Path

from models import Model3D, Paper, db
from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

FIXTURES = Path(__file__).parent / "fixtures" / "step"


def _project(client, title="CAD Project"):
    register(client)
    client.post("/papers/new", data={"title": title}, follow_redirects=True)
    with client.application.app_context():
        return Paper.query.filter_by(title=title).one().slug


def _upload(client, slug, name, data, **extra):
    form = {"file": upload_file_bytes(data, name), "compliance_confirm": "yes"}
    form.update(extra)
    return client.post(f"/papers/{slug}/upload-model", data=form, content_type="multipart/form-data", follow_redirects=True)


def _material_names(glb_path):
    raw = Path(glb_path).read_bytes()
    length = struct.unpack("<I", raw[12:16])[0]
    return [m.get("name") for m in json.loads(raw[20:20 + length]).get("materials", [])]


def test_colored_step_assembly_becomes_layers(client):
    slug = _project(client)
    _upload(client, slug, "gearbox.step", (FIXTURES / "assembly_colored.step").read_bytes())
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        assert model.source_format == "step"
        assert model.source_unit == "embedded"
        layers = model.layer_info["layers"]
        assert [layer["name"] for layer in layers] == ["Housing", "Shaft", "Cap"]
        assert len({layer["color"] for layer in layers}) == 3
        names = set(_material_names(model.glb_path))
        assert {"Housing", "Shaft", "Cap"} <= names
        model_id = model.id
    html = client.get(f"/view/{model_id}").get_data(as_text=True)
    assert 'id="layersBtn"' in html and 'id="layersPanel"' in html
    assert '"name": "Shaft"' in html


def test_same_colour_parts_are_not_merged_by_the_optimizer(client):
    slug = _project(client)
    _upload(client, slug, "plain.step", (FIXTURES / "assembly_same_color.step").read_bytes())
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        assert [layer["name"] for layer in model.layer_info["layers"]] == ["Housing", "Shaft", "Cap"]
        assert {"Housing", "Shaft", "Cap"} <= set(_material_names(model.glb_path))


def test_single_part_stp_has_no_layer_panel(client):
    slug = _project(client)
    _upload(client, slug, "box.stp", (FIXTURES / "box_plain.stp").read_bytes())
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        assert model.source_format == "step"
        assert model.layer_info is None
        assert model.dimensions_cm
        model_id = model.id
    assert 'id="layersBtn"' not in client.get(f"/view/{model_id}").get_data(as_text=True)


def test_invalid_step_is_rejected_at_upload(client):
    slug = _project(client)
    html = _upload(client, slug, "broken.step", b"not a step file").get_data(as_text=True)
    assert "ISO-10303-21" in html
    with client.application.app_context():
        assert Model3D.query.count() == 0


def test_replacing_with_step_and_back_updates_layers(client):
    slug = _project(client)
    _upload(client, slug, "part.stl", valid_ascii_stl_bytes(), source_unit="mm")
    with client.application.app_context():
        model = Model3D.query.one()
        model.license_type = "academic"  # roomy plan; replacement checks the plan's size limit
        db.session.commit()
        model_id = model.id
        assert model.layer_info is None

    def replace(name, data):
        return client.post(
            f"/models/{model_id}/replace",
            data={"file": upload_file_bytes(data, name), "compliance_confirm": "yes"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )

    replace("assembly.step", (FIXTURES / "assembly_colored.step").read_bytes())
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.source_format == "step" and model.source_unit == "embedded"
        assert len(model.layer_info["layers"]) == 3
        assert os.path.exists(model.glb_path)

    replace("part.stl", valid_ascii_stl_bytes())
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.source_format == "stl"
        assert model.layer_info is None
