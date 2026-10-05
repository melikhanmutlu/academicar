"""Scratch files a dead worker leaves behind are swept once old enough, but
nothing a pending/processing job still needs."""
import os
import time

import pytest

import app as app_module
from models import ConversionJob, Model3D, Paper, db
from tests.conftest import create_user

HOUR = 3600


def _write(path, size=100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


def _age(path, hours):
    stamp = time.time() - hours * HOUR
    if os.path.isdir(path):
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files + dirs:
                os.utime(os.path.join(root, name), (stamp, stamp))
    os.utime(path, (stamp, stamp))


def _job(app, model_id, status, **payload):
    owner = create_user(email=f"{model_id}@example.com", username=model_id)
    paper = Paper(title=f"P {model_id}", slug=f"p-{model_id}", user_id=owner.id)
    db.session.add(paper)
    db.session.flush()
    db.session.add(Model3D(id=model_id, paper_id=paper.id, user_id=owner.id, glb_path="m.glb", processing_status="queued"))
    db.session.add(ConversionJob(job_type="model_upload", status=status, model_id=model_id, payload=payload))
    db.session.commit()


@pytest.fixture()
def folders(app):
    return (
        app.config["CONVERTED_FOLDER"],
        app.config["UPLOAD_FOLDER"],
        app.config["MEDICAL_STAGING_FOLDER"],
    )


def test_old_orphans_are_removed_and_everything_else_kept(app, folders):
    converted, uploads, staging = folders
    with app.app_context():
        removed_paths = [
            _write(os.path.join(converted, "m-old", ".medical_result_ab", "work", "vol.nii"), 1000),
            _write(os.path.join(converted, "m-old", "model.glb.step-tmp.glb"), 500),
            _write(os.path.join(converted, "m-old", "model.glb.optimized.glb"), 500),
            _write(os.path.join(uploads, "_replace_dead", "raw.stl"), 300),
            _write(os.path.join(staging, "orphan-id", "scan.zip"), 2000),
            _write(os.path.join(staging, "_replace_dead2", "scan.zip"), 2000),
        ]
        kept_paths = [
            _write(os.path.join(converted, "m-old", "model.glb")),
            _write(os.path.join(uploads, "m-old", "v1", "part.stl")),
            _write(os.path.join(converted, "m-new", ".medical_result_cd", "vol.nii")),  # fresh
            _write(os.path.join(converted, "m-new", "x.step-tmp.glb")),  # fresh
            _write(os.path.join(uploads, "_replace_live", "raw.stl")),  # fresh
            _write(os.path.join(staging, "fresh-id", "scan.zip")),  # fresh
        ]
        for path in removed_paths + [kept_paths[0], kept_paths[1]]:
            _age(path, 10)
        _age(os.path.join(converted, "m-old", ".medical_result_ab"), 10)
        _age(os.path.join(uploads, "_replace_dead"), 10)
        _age(os.path.join(staging, "orphan-id"), 10)
        _age(os.path.join(staging, "_replace_dead2"), 10)

        result = app_module.sweep_orphaned_temp_artifacts(app)

        assert result["removed"] == 6
        assert result["freed_bytes"] == 1000 + 500 + 500 + 300 + 2000 + 2000
        for path in removed_paths:
            assert not os.path.exists(path), path
        for path in kept_paths:
            assert os.path.exists(path), path


def test_default_age_is_six_hours(app, folders):
    converted, _uploads, _staging = folders
    assert app.config["TEMP_ARTIFACT_MAX_AGE_SECONDS"] == 6 * HOUR
    with app.app_context():
        young = _write(os.path.join(converted, "m", "a.step-tmp.glb"))
        old = _write(os.path.join(converted, "m", "b.step-tmp.glb"))
        _age(young, 5)
        _age(old, 7)
        app_module.sweep_orphaned_temp_artifacts(app)
        assert os.path.exists(young) and not os.path.exists(old)


def test_active_jobs_keep_their_artifacts(app, folders):
    converted, uploads, staging = folders
    with app.app_context():
        pending_staging = os.path.join(staging, "scan-pending")
        processing_converted = os.path.join(converted, "scan-processing")
        protected = [
            _write(os.path.join(pending_staging, "scan.zip")),
            _write(os.path.join(processing_converted, ".medical_result_zz", "vol.nii")),
            _write(os.path.join(processing_converted, "model.glb.step-tmp.glb")),
            _write(os.path.join(uploads, "_replace_pending", "raw.stl")),
            _write(os.path.join(converted, "by-model-id", "m.glb.optimized.glb")),
        ]
        doomed = [
            _write(os.path.join(staging, "scan-failed", "scan.zip")),
            _write(os.path.join(converted, "finished", "m.glb.optimized.glb")),
        ]
        for path in protected + doomed:
            _age(os.path.dirname(path), 12)
            _age(path, 12)
        _age(pending_staging, 12)
        _age(os.path.join(uploads, "_replace_pending"), 12)
        _age(os.path.join(staging, "scan-failed"), 12)
        _job(app, "scan-pending", "pending", upload_dir=pending_staging)
        _job(app, "scan-processing", "processing", converted_dir=processing_converted)
        _job(app, "replace-pending", "pending", upload_dir=os.path.join(uploads, "_replace_pending"))
        _job(app, "by-model-id", "pending")  # no paths: protected by model id
        _job(app, "scan-failed", "failed", upload_dir=os.path.join(staging, "scan-failed"))
        _job(app, "finished", "completed", converted_dir=os.path.join(converted, "finished"))

        result = app_module.sweep_orphaned_temp_artifacts(app)

        for path in protected:
            assert os.path.exists(path), path
        for path in doomed:
            assert not os.path.exists(path), path
        assert result["removed"] == 2


def test_sweep_logs_counts_without_paths(app, folders, caplog):
    import logging

    converted, _u, _s = folders
    with app.app_context():
        path = _write(os.path.join(converted, "m", "a.step-tmp.glb"), 123)
        _age(path, 9)
        with caplog.at_level(logging.INFO):
            app_module.sweep_orphaned_temp_artifacts(app)
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "1 temp artifact" in messages and "123" in messages
    assert "a.step-tmp.glb" not in messages


def test_sweep_with_nothing_to_do_and_disabled(app, folders):
    with app.app_context():
        assert app_module.sweep_orphaned_temp_artifacts(app) == {"removed": 0, "freed_bytes": 0}
        assert app_module.sweep_orphaned_temp_artifacts(app, max_age_seconds=0) == {"removed": 0, "freed_bytes": 0}


def test_worker_runs_the_sweep_at_startup(app, monkeypatch):
    """worker.main() calls the sweep + low-disk warning right away."""
    import worker

    calls = []
    monkeypatch.setattr(worker, "create_app", lambda: app)
    monkeypatch.setattr(worker, "sweep_orphaned_temp_artifacts", lambda a: calls.append("sweep") or {"removed": 0, "freed_bytes": 0})
    monkeypatch.setattr(worker, "warn_if_storage_low", lambda a: calls.append("warn"))

    def stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(worker.time, "sleep", stop)
    monkeypatch.setattr(worker, "run_next_conversion_job", lambda a: False)
    monkeypatch.setattr(worker, "process_next_attachment_preview", lambda a: False)
    monkeypatch.setattr(worker, "run_scheduled_backups", lambda a: None)
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    assert calls == ["sweep", "warn"]
