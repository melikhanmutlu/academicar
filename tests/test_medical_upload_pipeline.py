"""Medical uploads through the real pipeline: consent, detection, job, layers, raw-data deletion."""
import os

import nibabel as nib
import numpy as np

from models import Model3D, ModelVersion, Paper, db
from tests.conftest import register
from tests.medical_fixtures import PATIENT_NAME, sphere, write_ct_series, zip_folder


def _project(client, title="Scan Project"):
    register(client)
    client.post("/papers/new", data={"title": title}, follow_redirects=True)
    with client.application.app_context():
        return Paper.query.filter_by(title=title).one().slug


def _upload(client, slug, path, name, **extra):
    form = {"compliance_confirm": "yes", "medical_confirm": "yes"}
    form.update(extra)
    with open(path, "rb") as handle:
        form["file"] = (handle, name)
        return client.post(f"/papers/{slug}/upload-model", data=form, content_type="multipart/form-data", follow_redirects=True)


def _ct_zip(tmp_path):
    write_ct_series(tmp_path / "series")
    return zip_folder(tmp_path / "series", tmp_path / "ct.zip")


def _labelmap(tmp_path):
    shape = (64, 64, 64)
    vol = np.zeros(shape, dtype=np.uint8)
    vol[sphere(shape, (20, 20, 20), 8)] = 1
    vol[sphere(shape, (44, 44, 44), 8)] = 2
    path = tmp_path / "seg.nii.gz"
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(path))
    return str(path)


def test_dicom_series_zip_becomes_a_model_and_the_raw_scan_is_deleted(client, tmp_path):
    slug = _project(client)
    _upload(client, slug, _ct_zip(tmp_path), "ct.zip", medical_preset="bone")
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        assert model.source_format == "dicom" and model.source_unit == "embedded"
        assert model.original_source_path is None and model.current_source_path is None
        assert ModelVersion.query.one().source_path is None
        assert not os.path.exists(os.path.join(client.application.config["UPLOAD_FOLDER"], model.id))
        assert os.path.exists(model.glb_path)
        assert model.dimensions_cm  # ~3 cm sphere
        with open(model.glb_path, "rb") as handle:
            assert PATIENT_NAME.encode() not in handle.read()
        model_id = model.id
    html = client.get(f"/view/{model_id}").get_data(as_text=True)
    assert "data-medical-note" in html and "not for diagnosis" in html


def test_segmentation_labels_become_layers_with_volumes(client, tmp_path):
    slug = _project(client)
    _upload(client, slug, _labelmap(tmp_path), "seg.nii.gz")
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        assert model.source_format == "segmentation"
        layers = model.layer_info["layers"]
        assert [layer["name"] for layer in layers] == ["Label 1", "Label 2"]
        # sphere r=8 voxels of 1 mm -> ~2.1 mL
        assert all(1.5 < layer["volume_ml"] < 2.8 for layer in layers)
        model_id = model.id
    html = client.get(f"/view/{model_id}").get_data(as_text=True)
    assert 'id="layersPanel"' in html and '"volume_ml"' in html
    assert "Volumes are estimated from the segmentation" in html


def test_medical_upload_needs_its_own_confirmation(client, tmp_path):
    slug = _project(client)
    html = _upload(client, slug, _labelmap(tmp_path), "seg.nii.gz", medical_confirm="").get_data(as_text=True)
    assert "patient-identifying information" in html
    with client.application.app_context():
        assert Model3D.query.count() == 0


def test_single_dicom_slice_and_unknown_zip_are_rejected(client, tmp_path):
    slug = _project(client)
    write_ct_series(tmp_path / "series", n_slices=3)
    one = sorted(os.listdir(tmp_path / "series"))[0]
    html = _upload(client, slug, str(tmp_path / "series" / one), "slice.dcm").get_data(as_text=True)
    assert "whole DICOM series as a ZIP" in html
    import zipfile

    bogus = tmp_path / "notes.zip"
    with zipfile.ZipFile(bogus, "w") as zf:
        zf.writestr("readme.txt", "hello")
    _upload(client, slug, str(bogus), "notes.zip")
    with client.application.app_context():
        assert Model3D.query.count() == 0
        uploads = client.application.config["UPLOAD_FOLDER"]
        assert not os.path.isdir(uploads) or not os.listdir(uploads)


def test_raw_scan_size_cap(client, tmp_path, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "MEDICAL_UPLOAD_MAX_BYTES", 1024)
    slug = _project(client)
    html = _upload(client, slug, _ct_zip(tmp_path), "ct.zip", medical_preset="bone").get_data(as_text=True)
    assert "the limit is" in html
    with client.application.app_context():
        assert Model3D.query.count() == 0


def test_invalid_preset_is_rejected(client, tmp_path):
    slug = _project(client)
    html = _upload(client, slug, _ct_zip(tmp_path), "ct.zip", medical_preset="lungs").get_data(as_text=True)
    assert "Choose what to extract" in html
    with client.application.app_context():
        assert Model3D.query.count() == 0


def test_failed_scan_conversion_still_deletes_the_raw_upload(client, tmp_path):
    slug = _project(client)
    write_ct_series(tmp_path / "series", background=-1000, fg=-1000)  # nothing above the bone threshold
    _upload(client, slug, zip_folder(tmp_path / "series", tmp_path / "empty.zip"), "empty.zip", medical_preset="bone")
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "failed"
        assert "No structure matched" in (model.processing_error or "")
        assert not os.path.exists(os.path.join(client.application.config["UPLOAD_FOLDER"], model.id))


def test_replacing_a_mesh_with_a_segmentation(client, tmp_path):
    from tests.conftest import upload_file_bytes, valid_ascii_stl_bytes

    slug = _project(client)
    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "part.stl"), "compliance_confirm": "yes", "source_unit": "mm"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with client.application.app_context():
        model_id = Model3D.query.one().id
    with open(_labelmap(tmp_path), "rb") as handle:
        client.post(
            f"/models/{model_id}/replace",
            data={"file": (handle, "seg.nii.gz"), "compliance_confirm": "yes", "medical_confirm": "yes"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.source_format == "segmentation"
        assert len(model.layer_info["layers"]) == 2
        assert model.current_source_path is None
        assert not os.path.isdir(os.path.join(client.application.config["UPLOAD_FOLDER"], model_id, "v2"))
