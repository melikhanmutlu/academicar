"""Resumable chunked uploads: session API, chunk handling, finalising through the normal pipeline."""
import os
import time

import pytest

import app as app_module
import uploads as uploads_module
from models import Model3D, Paper, db
from tests.conftest import login, register, valid_ascii_stl_bytes
from tests.medical_fixtures import write_ct_series, zip_folder


def _project(client, title="Chunk Project"):
    register(client)
    client.post("/papers/new", data={"title": title}, follow_redirects=True)
    with client.application.app_context():
        return Paper.query.filter_by(title=title).one().slug


def _start(client, filename, size, **target):
    return client.post("/uploads", json={"filename": filename, "size": size, "target": target})


def _put(client, upload_id, offset, data):
    return client.put(f"/uploads/{upload_id}?offset={offset}", data=data, content_type="application/octet-stream")


def _send(client, upload_id, data, chunk=1024):
    for offset in range(0, len(data), chunk):
        assert _put(client, upload_id, offset, data[offset : offset + chunk]).status_code == 200


def _staging(client, medical=False):
    return client.application.config["MEDICAL_STAGING_FOLDER" if medical else "UPLOAD_STAGING_FOLDER"]


def _ct_zip(tmp_path):
    write_ct_series(tmp_path / "series")
    return (tmp_path / "ct.zip", zip_folder(tmp_path / "series", tmp_path / "ct.zip"))


def _read(path):
    with open(path, "rb") as handle:
        return handle.read()


# --- session creation -------------------------------------------------------


def test_session_requires_login(client):
    response = _start(client, "a.stl", 100, new_project=True)
    assert response.status_code == 401


def test_session_for_paper_returns_id_and_chunk_size(client):
    slug = _project(client)
    response = _start(client, "part.stl", 500, paper_slug=slug)
    assert response.status_code == 201
    body = response.get_json()
    assert body["chunk_size"] == 8 * 1024 * 1024 and body["received"] == 0
    assert os.path.isdir(os.path.join(_staging(client), body["upload_id"]))


def test_session_rejects_foreign_project_and_unknown_targets(client, app):
    slug = _project(client)
    other = app.test_client()
    register(other, email="other@example.com", username="Other")
    assert _start(other, "a.stl", 10, paper_slug=slug).status_code == 403
    assert _start(other, "a.stl", 10, paper_slug="nope").status_code == 404
    assert _start(other, "a.stl", 10, model_id="nope").status_code == 404
    assert _start(other, "a.stl", 10).status_code == 400


def test_session_rejects_bad_extension_and_empty_file(client):
    slug = _project(client)
    assert _start(client, "notes.txt", 10, paper_slug=slug).status_code == 400
    assert _start(client, "a.stl", 0, paper_slug=slug).status_code == 400


def test_session_applies_plan_limit_for_models(client):
    slug = _project(client)
    too_big = 10**12
    response = _start(client, "huge.glb", too_big, paper_slug=slug)
    assert response.status_code == 413 and "too large" in response.get_json()["error"]


def test_session_applies_medical_cap_not_the_plan_limit(client, monkeypatch):
    slug = _project(client)
    monkeypatch.setattr(app_module, "MEDICAL_UPLOAD_MAX_BYTES", 1000)
    assert _start(client, "scan.zip", 2000, paper_slug=slug).status_code == 413
    assert _start(client, "scan.zip", 900, paper_slug=slug).status_code == 201


def test_session_refuses_early_when_disk_is_short(client, monkeypatch):
    from collections import namedtuple

    slug = _project(client)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("shutil.disk_usage", lambda path: Usage(10**12, 0, 1000))
    response = _start(client, "part.stl", 5000, paper_slug=slug)
    assert response.status_code == 507
    assert "Storage is temporarily full" in response.get_json()["error"]


def test_medical_session_is_staged_outside_uploads_with_a_neutral_name(client):
    slug = _project(client)
    upload_id = _start(client, "Smith_John_1970.zip", 100, paper_slug=slug).get_json()["upload_id"]
    directory = os.path.join(_staging(client, medical=True), upload_id)
    assert os.path.isdir(directory)
    assert not os.path.exists(os.path.join(_staging(client), upload_id))
    on_disk = "".join(_read(os.path.join(directory, name)).decode("utf-8", "ignore") for name in os.listdir(directory))
    assert "Smith" not in on_disk and "1970" not in on_disk
    assert not any("Smith" in name for name in os.listdir(directory))


def test_open_upload_cap(client, monkeypatch):
    slug = _project(client)
    monkeypatch.setattr(uploads_module, "MAX_ACTIVE_UPLOADS_PER_USER", 2)
    assert _start(client, "a.stl", 10, paper_slug=slug).status_code == 201
    assert _start(client, "a.stl", 10, paper_slug=slug).status_code == 201
    assert _start(client, "a.stl", 10, paper_slug=slug).status_code == 429


# --- chunks -------------------------------------------------------------------


def test_chunks_append_and_report_received(client):
    slug = _project(client)
    data = os.urandom(3000)
    upload_id = _start(client, "part.stl", len(data), paper_slug=slug).get_json()["upload_id"]
    first = _put(client, upload_id, 0, data[:1000])
    assert first.get_json()["received"] == 1000
    assert client.get(f"/uploads/{upload_id}").get_json()["received"] == 1000
    last = _put(client, upload_id, 1000, data[1000:]).get_json()
    assert last["received"] == 3000 and last["size"] == 3000
    assert _read(os.path.join(_staging(client), upload_id, "data.bin")) == data


def test_retrying_a_chunk_is_idempotent(client):
    slug = _project(client)
    data = os.urandom(2000)
    upload_id = _start(client, "part.stl", len(data), paper_slug=slug).get_json()["upload_id"]
    assert _put(client, upload_id, 0, data[:1000]).get_json()["received"] == 1000
    again = _put(client, upload_id, 0, data[:1000])  # reply was lost, client resends
    assert again.status_code == 200 and again.get_json()["received"] == 1000
    # A retry that overlaps and extends past what is stored only appends the new tail.
    assert _put(client, upload_id, 500, data[500:1500]).get_json()["received"] == 1500
    assert _put(client, upload_id, 1500, data[1500:]).get_json()["received"] == 2000
    assert _read(os.path.join(_staging(client), upload_id, "data.bin")) == data


def test_retry_with_different_bytes_is_refused(client):
    slug = _project(client)
    upload_id = _start(client, "part.stl", 2000, paper_slug=slug).get_json()["upload_id"]
    _put(client, upload_id, 0, b"a" * 1000)
    response = _put(client, upload_id, 0, b"b" * 1000)
    assert response.status_code == 409 and response.get_json()["received"] == 1000


def test_offset_gap_is_a_409_with_the_server_position(client):
    slug = _project(client)
    upload_id = _start(client, "part.stl", 3000, paper_slug=slug).get_json()["upload_id"]
    _put(client, upload_id, 0, b"x" * 1000)
    response = _put(client, upload_id, 2000, b"x" * 500)
    assert response.status_code == 409 and response.get_json()["received"] == 1000


def test_growth_beyond_declared_size_is_refused(client):
    slug = _project(client)
    upload_id = _start(client, "part.stl", 1500, paper_slug=slug).get_json()["upload_id"]
    _put(client, upload_id, 0, b"x" * 1000)
    response = _put(client, upload_id, 1000, b"x" * 1000)
    assert response.status_code == 413
    assert client.get(f"/uploads/{upload_id}").get_json()["received"] == 1000


def test_chunk_larger_than_the_chunk_size_is_refused(client, monkeypatch):
    slug = _project(client)
    monkeypatch.setattr(uploads_module, "CHUNK_SIZE", 100)
    upload_id = _start(client, "part.stl", 1000, paper_slug=slug).get_json()["upload_id"]
    assert _put(client, upload_id, 0, b"x" * 101).status_code == 413


def test_missing_or_bad_offset_is_a_400(client):
    slug = _project(client)
    upload_id = _start(client, "part.stl", 1000, paper_slug=slug).get_json()["upload_id"]
    assert client.put(f"/uploads/{upload_id}", data=b"x").status_code == 400
    assert client.put(f"/uploads/{upload_id}?offset=-1", data=b"x").status_code == 400


def test_chunk_rechecks_free_space(client, monkeypatch):
    from collections import namedtuple

    slug = _project(client)
    upload_id = _start(client, "part.stl", 4000, paper_slug=slug).get_json()["upload_id"]
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("shutil.disk_usage", lambda path: Usage(10**12, 0, 100))
    response = _put(client, upload_id, 0, b"x" * 1000)
    assert response.status_code == 507
    assert client.get(f"/uploads/{upload_id}").get_json()["received"] == 0


def test_only_the_owner_can_touch_an_upload(client, app):
    slug = _project(client)
    upload_id = _start(client, "part.stl", 1000, paper_slug=slug).get_json()["upload_id"]
    other = app.test_client()
    register(other, email="other@example.com", username="Other")
    assert _put(other, upload_id, 0, b"x").status_code == 404
    assert other.get(f"/uploads/{upload_id}").status_code == 404
    assert other.delete(f"/uploads/{upload_id}").get_json() == {"ok": True}
    assert os.path.isdir(os.path.join(_staging(client), upload_id))  # still there: not theirs


def test_cancel_deletes_the_staged_files(client):
    slug = _project(client)
    upload_id = _start(client, "part.stl", 1000, paper_slug=slug).get_json()["upload_id"]
    _put(client, upload_id, 0, b"x" * 500)
    assert client.delete(f"/uploads/{upload_id}").status_code == 200
    assert not os.path.exists(os.path.join(_staging(client), upload_id))
    assert client.get(f"/uploads/{upload_id}").status_code == 404
    # POST alias
    upload_id = _start(client, "part.stl", 1000, paper_slug=slug).get_json()["upload_id"]
    assert client.post(f"/uploads/{upload_id}/cancel").status_code == 200
    assert not os.path.exists(os.path.join(_staging(client), upload_id))


def test_a_busy_upload_answers_503_to_retry(client):
    slug = _project(client)
    upload_id = _start(client, "part.stl", 1000, paper_slug=slug).get_json()["upload_id"]
    os.mkdir(os.path.join(_staging(client), upload_id, ".lock"))
    response = _put(client, upload_id, 0, b"x" * 10)
    assert response.status_code == 503


# --- csrf ---------------------------------------------------------------------


def test_chunk_requests_need_a_csrf_token(app):
    client = app.test_client()
    slug = _project(client)
    upload_id = _start(client, "a.stl", 10, paper_slug=slug).get_json()["upload_id"]
    app.config["WTF_CSRF_ENABLED"] = True  # the logged-in session now has to prove itself
    stale = {"X-CSRFToken": "stale"}  # what the page script sends once its token has expired
    for response in (
        client.post("/uploads", json={"filename": "a.stl", "size": 10, "target": {"paper_slug": slug}}, headers=stale),
        client.put(f"/uploads/{upload_id}?offset=0", data=b"x", headers=stale),
        client.delete(f"/uploads/{upload_id}", headers=stale),
    ):
        assert response.status_code == 400 and response.get_json()["code"] == "csrf_expired"


# --- finalising ---------------------------------------------------------------


def _finish_stl(client, slug, **form):
    data = valid_ascii_stl_bytes()
    upload_id = _start(client, "tetra.stl", len(data), paper_slug=slug).get_json()["upload_id"]
    _send(client, upload_id, data, chunk=300)
    payload = {"upload_id": upload_id, "compliance_confirm": "yes", "source_unit": "mm"}
    payload.update(form)
    return upload_id, client.post(f"/papers/{slug}/upload-model", data=payload, follow_redirects=True)


def test_finishing_a_chunked_stl_runs_the_normal_pipeline(client):
    slug = _project(client)
    upload_id, response = _finish_stl(client, slug, display_name="Tetra")
    assert b"Model upload accepted" in response.data
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        assert model.display_name == "Tetra" and model.source_format == "stl"
        assert os.path.exists(model.glb_path)
        assert model.processing_progress == 100 and model.processing_stage is None
    assert not os.path.exists(os.path.join(_staging(client), upload_id))


def test_finishing_still_requires_the_compliance_checkbox(client):
    slug = _project(client)
    upload_id, response = _finish_stl(client, slug, compliance_confirm="")
    assert b"You must confirm that the model is anonymized" in response.data
    with client.application.app_context():
        assert Model3D.query.count() == 0
    assert not os.path.exists(os.path.join(_staging(client), upload_id))  # failure removes the staged file


def test_incomplete_upload_is_rejected_and_removed(client):
    slug = _project(client)
    data = valid_ascii_stl_bytes()
    upload_id = _start(client, "tetra.stl", len(data), paper_slug=slug).get_json()["upload_id"]
    _put(client, upload_id, 0, data[:100])
    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"upload_id": upload_id, "compliance_confirm": "yes", "source_unit": "mm"},
        follow_redirects=True,
    )
    assert b"The upload is not complete" in response.data
    with client.application.app_context():
        assert Model3D.query.count() == 0
    assert not os.path.exists(os.path.join(_staging(client), upload_id))


def test_unknown_upload_id_is_rejected(client):
    slug = _project(client)
    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"upload_id": "0" * 32, "compliance_confirm": "yes", "source_unit": "mm"},
        follow_redirects=True,
    )
    assert b"no longer available" in response.data


def test_upload_id_cannot_be_used_by_another_user_or_another_project(client, app):
    slug = _project(client)
    data = valid_ascii_stl_bytes()
    upload_id = _start(client, "tetra.stl", len(data), paper_slug=slug).get_json()["upload_id"]
    _send(client, upload_id, data)
    client.post("/papers/new", data={"title": "Second"}, follow_redirects=True)
    with app.app_context():
        second = Paper.query.filter_by(title="Second").one().slug
    form = {"upload_id": upload_id, "compliance_confirm": "yes", "source_unit": "mm"}
    # Staged for the first project: not usable on the second.
    assert b"no longer available" in client.post(f"/papers/{second}/upload-model", data=form, follow_redirects=True).data
    # Nor by someone else (the upload is gone by now, so recreate it).
    upload_id = _start(client, "tetra.stl", len(data), paper_slug=slug).get_json()["upload_id"]
    _send(client, upload_id, data)
    other = app.test_client()
    register(other, email="other@example.com", username="Other")
    form["upload_id"] = upload_id
    assert other.post(f"/papers/{slug}/upload-model", data=form).status_code == 403
    with app.app_context():
        assert Model3D.query.count() == 0


def test_a_real_file_upload_still_works_unchanged(client):
    slug = _project(client)
    response = client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": (__import__("io").BytesIO(valid_ascii_stl_bytes()), "tetra.stl"),
            "compliance_confirm": "yes",
            "source_unit": "mm",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Model upload accepted" in response.data


def test_finishing_a_dicom_zip_converts_it_and_deletes_the_raw_scan(client, tmp_path):
    slug = _project(client)
    path, _ = _ct_zip(tmp_path)
    data = _read(path)
    upload_id = _start(client, "Patient_Name_CT.zip", len(data), paper_slug=slug).get_json()["upload_id"]
    _send(client, upload_id, data, chunk=1500)
    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"upload_id": upload_id, "compliance_confirm": "yes", "medical_confirm": "yes", "medical_preset": "bone"},
        follow_redirects=True,
    )
    assert b"Model upload accepted" in response.data
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        assert model.source_format == "dicom" and model.original_filename == "scan.zip"
        assert model.original_source_path is None
        assert os.path.exists(model.glb_path)
        assert not os.path.exists(os.path.join(client.application.config["UPLOAD_FOLDER"], model.id))
        assert not os.path.exists(os.path.join(client.application.config["MEDICAL_STAGING_FOLDER"], model.id))
    assert os.listdir(_staging(client, medical=True)) == []


def test_finishing_a_dicom_zip_still_needs_medical_confirm(client, tmp_path):
    slug = _project(client)
    data = _read(_ct_zip(tmp_path)[0])
    upload_id = _start(client, "ct.zip", len(data), paper_slug=slug).get_json()["upload_id"]
    _send(client, upload_id, data, chunk=1500)
    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"upload_id": upload_id, "compliance_confirm": "yes", "medical_preset": "bone"},
        follow_redirects=True,
    )
    assert b"patient-identifying information" in response.data
    with client.application.app_context():
        assert Model3D.query.count() == 0
    assert os.listdir(_staging(client, medical=True)) == []


def test_replace_with_a_chunked_upload(client):
    slug = _project(client)
    _finish_stl(client, slug)
    with client.application.app_context():
        model = Model3D.query.one()
        model_id, version = model.id, model.version
    data = valid_ascii_stl_bytes().replace(b"tetra", b"other")
    upload_id = _start(client, "other.stl", len(data), model_id=model_id).get_json()["upload_id"]
    _send(client, upload_id, data, chunk=200)
    # Staged for the replace of one model: another model's endpoint refuses it.
    response = client.post(
        f"/models/{model_id}/replace",
        data={"upload_id": upload_id, "compliance_confirm": "yes"},
        follow_redirects=True,
    )
    assert b"Model file replaced" in response.data
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.version == version + 1 and model.processing_status == "ready"
    assert not os.path.exists(os.path.join(_staging(client), upload_id))


def test_new_project_with_a_chunked_model(client):
    register(client)
    data = valid_ascii_stl_bytes()
    upload_id = _start(client, "tetra.stl", len(data), new_project=True).get_json()["upload_id"]
    _send(client, upload_id, data)
    response = client.post(
        "/papers/new",
        data={
            "title": "Chunked Project",
            "upload_id": upload_id,
            "compliance_confirm": "yes",
            "source_unit": "mm",
        },
        follow_redirects=True,
    )
    assert b"Model upload accepted" in response.data
    with client.application.app_context():
        assert Model3D.query.count() == 1 and Paper.query.filter_by(title="Chunked Project").count() == 1
    assert not os.path.exists(os.path.join(_staging(client), upload_id))


def test_new_project_validation_error_discards_the_staged_file(client):
    register(client)
    data = valid_ascii_stl_bytes()
    upload_id = _start(client, "tetra.stl", len(data), new_project=True).get_json()["upload_id"]
    _send(client, upload_id, data)
    client.post("/papers/new", data={"title": "", "upload_id": upload_id}, follow_redirects=True)
    assert not os.path.exists(os.path.join(_staging(client), upload_id))


# --- housekeeping -------------------------------------------------------------


def test_sweep_removes_stale_staged_uploads_but_keeps_fresh_ones(client):
    slug = _project(client)
    stale = _start(client, "a.stl", 10, paper_slug=slug).get_json()["upload_id"]
    stale_scan = _start(client, "a.zip", 10, paper_slug=slug).get_json()["upload_id"]
    fresh = _start(client, "b.stl", 10, paper_slug=slug).get_json()["upload_id"]
    old = time.time() - 48 * 3600
    for root, upload_id in ((_staging(client), stale), (_staging(client, medical=True), stale_scan)):
        directory = os.path.join(root, upload_id)
        for name in os.listdir(directory):
            os.utime(os.path.join(directory, name), (old, old))
        os.utime(directory, (old, old))
    with client.application.app_context():
        result = app_module.sweep_orphaned_temp_artifacts(client.application)
    assert result["removed"] == 2
    assert not os.path.exists(os.path.join(_staging(client), stale))
    assert not os.path.exists(os.path.join(_staging(client, medical=True), stale_scan))
    assert os.path.isdir(os.path.join(_staging(client), fresh))


def test_staging_folders_are_outside_the_backup_folders(app):
    staging = os.path.abspath(app.config["UPLOAD_STAGING_FOLDER"])
    for key in ("UPLOAD_FOLDER", "CONVERTED_FOLDER", "QR_FOLDER", "PDF_FOLDER"):
        folder = os.path.abspath(app.config[key])
        assert not staging.startswith(folder + os.sep) and staging != folder


# --- csrf handler ---------------------------------------------------------------


def test_cut_off_multipart_upload_gets_the_interrupted_message(app):
    client = app.test_client()
    register(client)
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post(
        "/papers/new",
        data={"model_file": (__import__("io").BytesIO(b"x" * 10), "a.stl")},
        content_type="multipart/form-data",
        headers={"Referer": "http://localhost/dashboard"},
    )
    assert response.status_code == 303
    follow = client.get(response.headers["Location"])
    assert b"The upload was interrupted before it finished. Please try again." in follow.data


def test_other_csrf_failures_keep_the_open_too_long_message(app):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    response = client.post("/auth/login", data={"email": "a@b.c"}, headers={"Referer": "http://localhost/auth/login"})
    assert response.status_code == 303
    follow = client.get(response.headers["Location"])
    assert b"open too long" in follow.data
