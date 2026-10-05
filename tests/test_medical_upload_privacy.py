"""Medical uploads: no patient data in filenames, raw scans kept out of backups,
institutional quota ignores the raw scan, multi-structure DICOM presets."""
import json
import os
import zipfile

import pytest

import app as app_module
from models import AuditLog, ConversionJob, Model3D, ModelVersion, Paper, User, db
from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes
from tests.test_institutions import add_member, create_institution
from tests.test_medical_upload_pipeline import _ct_zip, _labelmap, _project, _upload

NEUTRAL_NAMES = {"scan.zip", "scan.dcm", "segmentation.nii.gz", "segmentation.nii", "segmentation.nrrd", "segmentation.seg.nrrd"}


@pytest.fixture()
def queued(monkeypatch):
    """Leave the conversion job pending, as the production web process does."""
    monkeypatch.setattr(app_module, "process_model_upload_job", lambda *args, **kwargs: None)


def _files_under(folder):
    for root, _dirs, files in os.walk(folder):
        for name in files:
            yield os.path.join(root, name)


def _assert_no_surname(app, needle="Doe"):
    """The user's filename is in no row, payload, audit entry or stored file."""
    for model in Model3D.query.all():
        assert needle not in (model.original_filename or "")
        assert needle not in (model.display_name or "")
        assert needle not in (model.original_source_path or "")
        assert needle not in (model.current_source_path or "")
    for job in ConversionJob.query.all():
        assert needle not in json.dumps(job.payload)
    for version in ModelVersion.query.all():
        assert needle not in (version.source_path or "")
    for entry in AuditLog.query.all():
        assert needle not in json.dumps(entry.details or {})
    for folder in (app.config["UPLOAD_FOLDER"], app.config["MEDICAL_STAGING_FOLDER"]):
        for path in _files_under(folder):
            assert needle not in path


@pytest.mark.parametrize("name, kind", [("Doe_Jane_CT.zip", "dicom"), ("Doe_Jane_seg.nii.gz", "segmentation")])
def test_medical_filename_is_never_stored(client, tmp_path, queued, name, kind):
    slug = _project(client)
    source = _ct_zip(tmp_path) if kind == "dicom" else _labelmap(tmp_path)
    _upload(client, slug, source, name, medical_preset="bone")
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.original_filename in NEUTRAL_NAMES
        assert model.original_filename.endswith(".zip" if kind == "dicom" else ".nii.gz")
        assert model.display_name == ("CT/MR scan" if kind == "dicom" else "Segmentation")
        _assert_no_surname(client.application)
        staged = os.path.join(client.application.config["MEDICAL_STAGING_FOLDER"], model.id, model.original_filename)
        assert os.path.isfile(staged)


def test_medical_filename_not_stored_after_conversion_and_name_choice_is_kept(client, tmp_path):
    slug = _project(client)
    _upload(client, slug, _ct_zip(tmp_path), "Doe_Jane_CT.zip", medical_preset="bone", display_name="Skull model")
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        assert model.display_name == "Skull model"
        assert model.original_filename == "scan.zip"
        _assert_no_surname(client.application)
        model_id = model.id
    assert "Doe" not in client.get(f"/view/{model_id}").get_data(as_text=True)


def test_replacing_with_a_scan_stores_no_filename_and_keeps_the_staged_scan(client, tmp_path, queued):
    slug = _project(client)
    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "part.stl"), "compliance_confirm": "yes", "source_unit": "mm"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with client.application.app_context():
        model_id = Model3D.query.one().id
    with open(_ct_zip(tmp_path), "rb") as handle:
        client.post(
            f"/models/{model_id}/replace",
            data={"file": (handle, "Doe_Jane_CT.zip"), "compliance_confirm": "yes", "medical_confirm": "yes", "medical_preset": "bone"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.original_filename == "scan.zip"
        assert model.display_name == "CT/MR scan"
        _assert_no_surname(client.application)
        job = ConversionJob.query.filter_by(job_type="model_replace").one()
        staging = client.application.config["MEDICAL_STAGING_FOLDER"]
        assert job.payload["upload_dir"].startswith(staging)
        # The worker has not run yet and still needs the raw scan.
        assert os.path.isfile(job.payload["source_path"])


def test_queued_raw_scan_is_not_in_a_backup(client, tmp_path, queued, monkeypatch):
    monkeypatch.setitem(client.application.config, "STORAGE_ROOT", str(tmp_path / "storage"))
    slug = _project(client)
    _upload(client, slug, _ct_zip(tmp_path), "Doe_Jane_CT.zip", medical_preset="bone")
    with client.application.app_context():
        model = Model3D.query.one()
        uploads = client.application.config["UPLOAD_FOLDER"]
        staging = client.application.config["MEDICAL_STAGING_FOLDER"]
        assert os.path.isfile(os.path.join(staging, model.id, "scan.zip"))
        assert not any(True for _ in _files_under(uploads))
        filename = app_module.create_backup_archive(client.application)
        archive = os.path.join(app_module.backup_folder(client.application), filename)
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        assert not any(name.endswith("scan.zip") or "medical_staging" in name for name in names)


def test_admin_storage_stats_survive_the_staging_folder(client, tmp_path, queued):
    from tests.test_admin_panel import _make_admin

    slug = _project(client)
    _upload(client, slug, _ct_zip(tmp_path), "ct.zip", medical_preset="bone")
    client.post("/auth/logout")
    _make_admin(client)
    for page in ("/admin", "/admin/storage", "/admin/security"):
        assert client.get(page).status_code == 200


def _member_with_quota(client, quota):
    register(client)
    with client.application.app_context():
        add_member(create_institution(quota_storage_bytes=quota), User.query.one())


def test_institution_funds_a_scan_larger_than_its_remaining_quota(client, tmp_path):
    scan = _ct_zip(tmp_path)
    quota = os.path.getsize(scan) // 2
    _member_with_quota(client, quota)
    client.post("/papers/new", data={"title": "Inst Scan"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Inst Scan").one().slug
    _upload(client, slug, scan, "ct.zip", medical_preset="bone")
    with client.application.app_context():
        model = Model3D.query.one()
        assert 0 < quota < os.path.getsize(scan)
        assert model.license_type == "institutional", model.processing_error
        assert model.institution_id is not None


def test_replacing_with_a_scan_ignores_the_storage_growth_check(client, tmp_path):
    scan = _ct_zip(tmp_path)
    _member_with_quota(client, 5 * 1024 * 1024)
    client.post("/papers/new", data={"title": "Inst Replace"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Inst Replace").one().slug
    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "part.stl"), "compliance_confirm": "yes", "source_unit": "mm"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with client.application.app_context():
        from models import Institution

        model = Model3D.query.one()
        assert model.license_type == "institutional"
        model_id = model.id
        Institution.query.one().quota_storage_bytes = os.path.getsize(scan) // 2
        db.session.commit()
    with open(scan, "rb") as handle:
        client.post(
            f"/models/{model_id}/replace",
            data={"file": (handle, "ct.zip"), "compliance_confirm": "yes", "medical_confirm": "yes", "medical_preset": "bone"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).source_format == "dicom"


def _payload_preset(client, tmp_path, title, presets):
    # The free plan holds one model per project, so each call gets its own.
    client.post("/papers/new", data={"title": title}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title=title).one().slug
    _upload(client, slug, _ct_zip(tmp_path), "ct.zip", medical_preset=presets)
    with client.application.app_context():
        job = ConversionJob.query.order_by(ConversionJob.id.desc()).first()
        return job.payload.get("medical_preset") if job else None


def test_several_presets_become_one_canonical_comma_string(client, tmp_path, queued):
    _project(client)
    assert _payload_preset(client, tmp_path, "P1", ["bone", "skin"]) == "bone,skin"
    assert _payload_preset(client, tmp_path, "P2", ["auto", "skin", "bone", "skin"]) == "bone,skin,auto"
    assert _payload_preset(client, tmp_path, "P3", "contrast") == "contrast"  # a single string still works


def test_unknown_or_missing_preset_is_rejected_for_a_dicom_zip(client, tmp_path, queued):
    slug = _project(client)
    html = _upload(client, slug, _ct_zip(tmp_path), "ct.zip", medical_preset=["bone", "lungs"]).get_data(as_text=True)
    assert "Choose what to extract" in html
    html = _upload(client, slug, _ct_zip(tmp_path), "ct.zip").get_data(as_text=True)
    assert "Choose what to extract" in html
    with client.application.app_context():
        assert Model3D.query.count() == 0
        assert not any(True for _ in _files_under(client.application.config["MEDICAL_STAGING_FOLDER"]))


def test_a_segmentation_needs_no_preset(client, tmp_path, queued):
    slug = _project(client)
    _upload(client, slug, _labelmap(tmp_path), "seg.nii.gz")
    with client.application.app_context():
        assert Model3D.query.one().source_format == "segmentation"
        assert "medical_preset" not in ConversionJob.query.one().payload


def test_upload_forms_offer_checkboxes_and_skip_filename_autofill(client):
    _project(client)
    html = client.get("/papers/new").get_data(as_text=True)
    assert '<select name="medical_preset"' not in html
    assert 'type="checkbox" name="medical_preset" value="bone" checked' in html
    for value in ("skin", "contrast", "auto"):
        assert f'name="medical_preset" value="{value}"' in html
    assert "isMedicalFile" in html
