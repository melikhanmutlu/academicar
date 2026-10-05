"""Live processing progress: worker milestones, the medical child-process channel, /models/<id>/status."""
import io
import os
import re
import time

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import text

import app as app_module
from converters.medical import cli, progress
from models import ConversionJob, Model3D, Paper, db
from tests.conftest import register, valid_ascii_stl_bytes
from tests.medical_fixtures import PATIENT_ID, PATIENT_NAME, write_ct_series, zip_folder


def _project(client, title="Progress Project"):
    register(client)
    client.post("/papers/new", data={"title": title}, follow_redirects=True)
    with client.application.app_context():
        return Paper.query.filter_by(title=title).one().slug


def _more_projects(client, count):
    slugs = []
    for index in range(count):
        client.post("/papers/new", data={"title": f"Extra {index}"}, follow_redirects=True)
        with client.application.app_context():
            slugs.append(Paper.query.filter_by(title=f"Extra {index}").one().slug)
    return slugs


def _upload_stl(client, slug, name="tetra.stl"):
    return client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": (io.BytesIO(valid_ascii_stl_bytes()), name),
            "compliance_confirm": "yes",
            "source_unit": "mm",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


@pytest.fixture()
def queued(monkeypatch):
    """Uploads create their ConversionJob but nothing runs it (a busy worker)."""
    monkeypatch.setattr(app_module, "process_model_upload_job", lambda *args, **kwargs: None)


@pytest.fixture()
def recorded(monkeypatch):
    calls = []
    real = app_module.report_processing_progress

    def spy(model_id, percent, stage=None):
        calls.append((percent, stage))
        real(model_id, percent, stage)

    monkeypatch.setattr(app_module, "report_processing_progress", spy)
    return calls


# --- helper ---------------------------------------------------------------------


def test_report_writes_progress_and_stage_and_throttles(client, monkeypatch):
    slug = _project(client)
    with client.application.app_context():
        app_module.report_processing_progress("x", 1, "warm up")  # unknown model: harmless
    _upload_stl(client, slug)
    with client.application.app_context():
        model_id = Model3D.query.one().id
        app_module.report_processing_progress(model_id, 41.6, "Reading DICOM slices 12 / 240")
        row = db.session.get(Model3D, model_id)
        db.session.refresh(row)
        assert (row.processing_progress, row.processing_stage) == (42, "Reading DICOM slices 12 / 240")
        # Same stage (digits aside) inside the throttle window: skipped.
        app_module.report_processing_progress(model_id, 50, "Reading DICOM slices 13 / 240")
        db.session.refresh(row)
        assert row.processing_progress == 42
        # A different stage always goes through.
        app_module.report_processing_progress(model_id, 55, "Smoothing and simplifying")
        db.session.refresh(row)
        assert (row.processing_progress, row.processing_stage) == (55, "Smoothing and simplifying")
        # Once the window has passed, the same stage updates again.
        time.sleep(app_module.PROGRESS_WRITE_INTERVAL_SECONDS + 0.05)
        app_module.report_processing_progress(model_id, 60, "Smoothing and simplifying")
        db.session.refresh(row)
        assert row.processing_progress == 60
        # Out-of-range values are clamped.
        app_module.report_processing_progress(model_id, 400, "Finishing")
        db.session.refresh(row)
        assert row.processing_progress == 100


def test_progress_does_not_disturb_the_callers_session(client):
    slug = _project(client)
    _upload_stl(client, slug)
    with client.application.app_context():
        model = Model3D.query.one()
        model.display_name = "Unsaved edit"  # pending change in the job's session
        app_module.report_processing_progress(model.id, 33, "Converting")
        assert model.display_name == "Unsaved edit" and db.session.dirty
        db.session.rollback()
        assert db.session.get(Model3D, model.id).processing_stage == "Converting"


# --- worker milestones ------------------------------------------------------------


def test_stl_upload_walks_through_the_worker_milestones(client, recorded):
    slug = _project(client)
    _upload_stl(client, slug)
    stages = [stage for _percent, stage in recorded]
    for expected in ("Converting", "Building layers", "Compressing geometry", "Finishing"):
        assert expected in stages, stages
    percents = [percent for percent, _stage in recorded]
    assert percents == sorted(percents) and 0 < percents[0] and percents[-1] < 100
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready"
        assert model.processing_progress == 100 and model.processing_stage is None


def test_failure_keeps_the_last_stage(client, recorded, monkeypatch):
    slug = _project(client)

    def boom(*args, **kwargs):
        raise app_module.GLBQualityError("simulated")

    monkeypatch.setattr(app_module, "finalize_converted_glb", boom)
    _upload_stl(client, slug)
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "failed"
        assert model.processing_stage == "Compressing geometry" == recorded[-1][1]
        assert 0 < model.processing_progress < 100
    payload = client.get(f"/models/{model.id}/status").get_json()
    assert payload["status"] == "failed" and payload["progress"] is None


def test_queued_model_starts_at_zero_and_waiting_for_the_converter(client, queued):
    slug = _project(client)
    _upload_stl(client, slug)
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "queued"
        assert (model.processing_progress, model.processing_stage) == (0, "Waiting for the converter")


# --- medical child process ---------------------------------------------------------


def _ct_zip(tmp_path, n_slices=40):
    write_ct_series(tmp_path / "series", n_slices=n_slices)
    return zip_folder(tmp_path / "series", tmp_path / "ct.zip")


def test_medical_child_reports_real_counts_to_its_progress_file(tmp_path, monkeypatch):
    zip_path = _ct_zip(tmp_path)
    seen = []
    real = progress.report

    def spy(percent, stage, *, force=False):
        seen.append((percent, stage))
        real(percent, stage, force=force)

    monkeypatch.setattr(progress, "report", spy)
    path = str(tmp_path / "progress.json")
    result = cli.run_conversion("dicom", str(zip_path), str(tmp_path / "out.glb"), "bone,skin", str(tmp_path), path)
    assert result["ok"], result["error"]
    stages = [stage for _p, stage in seen]
    assert "Unpacking the ZIP" in stages
    assert any(re.fullmatch(r"Unpacking files \d+ / \d+", s) for s in stages)
    assert any(re.fullmatch(r"Checking slices \d+ / 40", s) for s in stages)
    assert any(re.fullmatch(r"Reading DICOM slices \d+ / 40", s) for s in stages)
    assert "Extracting Bone surface (1 / 2)" in stages and "Extracting Skin surface (2 / 2)" in stages
    assert stages.count("Smoothing and simplifying") == 2
    assert stages[-1] == "Writing model"
    percents = [p for p, _s in seen]
    assert 0 <= min(percents) and max(percents) <= 100
    # the file the parent polls holds the last value, and reporting is off again afterwards
    assert progress.read(path) == (100.0, "Writing model")
    assert progress._state["path"] is None


def test_compressed_series_say_decoding(tmp_path, monkeypatch):
    from tests.medical_fixtures import compress_series

    folder = write_ct_series(tmp_path / "series")
    compress_series(folder, "RLELossless")
    zip_path = zip_folder(folder, tmp_path / "ct.zip")
    seen = []
    real = progress.report
    monkeypatch.setattr(progress, "report", lambda percent, stage, **kw: (seen.append(stage), real(percent, stage, **kw))[1])
    result = cli.run_conversion("dicom", str(zip_path), str(tmp_path / "out.glb"), "bone", str(tmp_path), str(tmp_path / "p.json"))
    assert result["ok"], result["error"]
    assert any(s.startswith("Decoding compressed slices") for s in seen)


def test_segmentation_progress_never_names_structures(tmp_path, monkeypatch):
    import nibabel as nib
    import numpy as np

    from tests.medical_fixtures import sphere

    vol = np.zeros((64, 64, 64), dtype=np.uint8)
    vol[sphere((64, 64, 64), (20, 20, 20), 8)] = 1
    path = tmp_path / "seg.nii.gz"
    nib.save(nib.Nifti1Image(vol, np.eye(4)), str(path))
    seen = []
    real = progress.report
    monkeypatch.setattr(progress, "report", lambda percent, stage, **kw: (seen.append(stage), real(percent, stage, **kw))[1])
    result = cli.run_conversion("segmentation", str(path), str(tmp_path / "out.glb"), None, str(tmp_path), str(tmp_path / "p.json"))
    assert result["ok"], result["error"]
    assert "Extracting structure surface (1 / 1)" in seen and not any("Label" in s for s in seen)


def test_progress_never_goes_backwards():
    progress.set_sink(None)
    try:
        progress.set_sink("/nonexistent-dir/p.json")
        progress.report(60, "a")
        progress.report(20, "b")
        assert progress._state["peak"] == 60
    finally:
        progress.set_sink(None)


def test_poller_hands_changes_to_the_callback(tmp_path):
    path = str(tmp_path / "p.json")
    progress.set_sink(path)
    got = []
    try:
        with progress.Poller(path, lambda percent, stage: got.append((percent, stage)), interval=0.05):
            progress.report(10, "Unpacking the ZIP", force=True)
            time.sleep(0.3)
            progress.report(60, "Writing model", force=True)
    finally:
        progress.set_sink(None)
    assert got[0] == (10.0, "Unpacking the ZIP") and got[-1] == (60.0, "Writing model")


def test_medical_upload_progress_flows_into_the_job_and_stays_anonymous(client, tmp_path, recorded):
    slug = _project(client)
    zip_path = _ct_zip(tmp_path)
    with open(zip_path, "rb") as handle:
        client.post(
            f"/papers/{slug}/upload-model",
            data={
                "file": (handle, "Smith_John_CT.zip"),
                "compliance_confirm": "yes",
                "medical_confirm": "yes",
                "medical_preset": "bone",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
    stages = [stage for _p, stage in recorded]
    assert "Converting" in stages and "Building layers" in stages
    # the poller thread hands over at least the child's last stage, mapped into the 5-65% slice
    child = [(p, s) for p, s in recorded if s == "Writing model"]
    assert child and all(5 <= p <= 65 for p, _s in child)
    assert not any(token in " ".join(s for s in stages if s) for token in ("Smith", "John", "scan.zip", PATIENT_NAME, PATIENT_ID))
    percents = [p for p, _s in recorded]
    assert max(percents) <= 100 and recorded[-1][1] == "Finishing"


def test_step_converter_reports_start_and_finish(tmp_path, monkeypatch):
    from converters.step_converter import STEPConverter

    seen = []
    converter = STEPConverter()
    converter.progress_callback = lambda percent, stage: seen.append((percent, stage))
    monkeypatch.setattr(converter, "_run_cascadio", lambda *a: True)
    monkeypatch.setattr(converter, "_post_process", lambda *a: False)
    monkeypatch.setattr(converter, "validate", lambda path: True)
    converter.convert(str(tmp_path / "a.step"), str(tmp_path / "a.glb"))
    assert [stage for _p, stage in seen] == ["Converting STEP geometry", "Checking the converted geometry"]


# --- /models/<id>/status -----------------------------------------------------------


def test_status_reports_progress_stage_and_queue_position(client, queued):
    slug = _project(client)
    _upload_stl(client, slug, "one.stl")
    _upload_stl(client, _more_projects(client, 1)[0], "two.stl")
    with client.application.app_context():
        first, second = Model3D.query.order_by(Model3D.created_at, Model3D.id).all()
        first_id, second_id = first.id, second.id
        row = db.session.get(Model3D, second_id)
    one = client.get(f"/models/{first_id}/status").get_json()
    two = client.get(f"/models/{second_id}/status").get_json()
    assert one["status"] == "queued" and one["progress"] == 0 and one["stage"] == "Waiting for the converter"
    assert {one["queue_position"], two["queue_position"]} == {1, 2}
    # the earlier job is ahead of the later one
    with client.application.app_context():
        jobs = {job.model_id: job for job in ConversionJob.query.all()}
        earlier = min(jobs.values(), key=lambda job: (job.created_at, job.id)).model_id
    assert client.get(f"/models/{earlier}/status").get_json()["queue_position"] == 1


def test_queue_position_counts_every_pending_job_ahead(client, queued):
    slug = _project(client)
    for name, target in zip(("a.stl", "b.stl", "c.stl"), [slug, *_more_projects(client, 2)]):
        _upload_stl(client, target, name)
    with client.application.app_context():
        order = [job.model_id for job in ConversionJob.query.order_by(ConversionJob.created_at, ConversionJob.id)]
    positions = [client.get(f"/models/{model_id}/status").get_json()["queue_position"] for model_id in order]
    assert positions == [1, 2, 3]


def test_a_running_job_has_no_queue_position(client, queued):
    slug = _project(client)
    _upload_stl(client, slug)
    with client.application.app_context():
        model = Model3D.query.one()
        model.processing_status = "processing"
        model.processing_progress, model.processing_stage = 47, "Compressing geometry"
        ConversionJob.query.one().status = "processing"
        db.session.commit()
        model_id = model.id
    payload = client.get(f"/models/{model_id}/status").get_json()
    assert payload["status"] == "processing" and payload["progress"] == 47
    assert payload["stage"] == "Compressing geometry" and payload["queue_position"] is None


def test_ready_models_report_no_progress(client):
    slug = _project(client)
    _upload_stl(client, slug)
    with client.application.app_context():
        model_id = Model3D.query.one().id
    payload = client.get(f"/models/{model_id}/status").get_json()
    assert payload["status"] == "ready" and payload["progress"] is None and payload["stage"] is None
    assert "queue_position" not in payload and payload["replacing"] is False


def test_a_replacement_in_progress_is_reported_while_the_old_model_stays_ready(client, monkeypatch):
    slug = _project(client)
    _upload_stl(client, slug)
    with client.application.app_context():
        model_id = Model3D.query.one().id
    monkeypatch.setattr(app_module, "process_model_upload_job", lambda *args, **kwargs: None)
    client.post(
        f"/models/{model_id}/replace",
        data={"file": (io.BytesIO(valid_ascii_stl_bytes()), "again.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    payload = client.get(f"/models/{model_id}/status").get_json()
    assert payload["status"] == "ready" and payload["replacing"] is True
    assert (payload["progress"], payload["stage"], payload["queue_position"]) == (0, "Waiting for the converter", 1)


def test_status_pages_poll_for_progress(client, queued):
    slug = _project(client)
    _upload_stl(client, slug)
    with client.application.app_context():
        model_id = Model3D.query.one().id
    detail = client.get(f"/papers/{slug}").get_data(as_text=True)
    assert "data-progress-panel" in detail and "Waiting for the converter" in detail
    edit = client.get(f"/models/{model_id}/edit").get_data(as_text=True)
    assert "data-progress-panel" in edit


# --- schema -------------------------------------------------------------------------


def test_migration_extends_the_single_head():
    script = ScriptDirectory(os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations"))
    assert script.get_revision("e8f9a0b1c2d3").down_revision == "d7e8f9a0b1c2"
    assert len(script.get_heads()) == 1


def test_sqlite_schema_helper_adds_the_progress_columns(app):
    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE models DROP COLUMN processing_progress"))
            connection.execute(text("ALTER TABLE models DROP COLUMN processing_stage"))
        app_module.ensure_sqlite_schema(app)
        with db.engine.begin() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(models)")).fetchall()}
        assert {"processing_progress", "processing_stage"} <= columns
        app_module.ensure_sqlite_schema(app)  # idempotent
