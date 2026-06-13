"""Tests for reclaim_stuck_conversion_jobs — recovering jobs wedged in
'processing' after a worker died mid-conversion."""
from datetime import UTC, datetime, timedelta

import app as app_module
from models import ConversionJob, Model3D, Paper, User, db


def _seed_job(app, *, started_at, attempts=1, max_attempts=3):
    with app.app_context():
        app.config["STUCK_JOB_TIMEOUT_SECONDS"] = 3600
        user = User(email="reap@example.com", username="R")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(title="T", slug="reap-paper", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="m-reap-1",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="c/m.glb",
            processing_status="processing",
        )
        db.session.add(model)
        job = ConversionJob(
            job_type="model_upload",
            status="processing",
            model_id=model.id,
            user_id=user.id,
            payload={"model_id": model.id},
            attempts=attempts,
            max_attempts=max_attempts,
            started_at=started_at,
        )
        db.session.add(job)
        db.session.commit()
        return job.id, model.id


def test_null_started_at_is_stamped_and_persisted(app):
    """Regression: a job with no started_at must get a stamp that is actually
    committed, so a subsequent pass can age it out (previously the stamp was
    discarded because the commit only ran when reclaimed > 0)."""
    job_id, _ = _seed_job(app, started_at=None)
    with app.app_context():
        assert app_module.reclaim_stuck_conversion_jobs(app) == 1
    with app.app_context():
        job = db.session.get(ConversionJob, job_id)
        assert job.started_at is not None  # persisted across the commit
        assert job.status == "processing"  # not yet aged out, just stamped


def test_stale_job_is_requeued(app):
    job_id, _ = _seed_job(app, started_at=datetime.now(UTC) - timedelta(hours=2), attempts=1)
    with app.app_context():
        assert app_module.reclaim_stuck_conversion_jobs(app) == 1
    with app.app_context():
        assert db.session.get(ConversionJob, job_id).status == "pending"


def test_stale_job_at_max_attempts_is_failed(app):
    job_id, model_id = _seed_job(
        app, started_at=datetime.now(UTC) - timedelta(hours=2), attempts=3, max_attempts=3
    )
    with app.app_context():
        assert app_module.reclaim_stuck_conversion_jobs(app) == 1
    with app.app_context():
        assert db.session.get(ConversionJob, job_id).status == "failed"
        assert db.session.get(Model3D, model_id).processing_status == "failed"
