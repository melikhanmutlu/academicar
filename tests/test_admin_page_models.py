"""Admin models page: licence save safety, health metrics, force-rebuild paging."""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from models import ConversionJob, Model3D, Paper, db
from tests.test_admin_phase0_fixes import _make_admin, _seed_model


def _job(model_id, job_type, status, **kw):
    db.session.add(ConversionJob(job_type=job_type, status=status, model_id=model_id, payload={}, **kw))


def test_license_save_rejects_unknown_plan(client):
    _make_admin(client)
    mid = _seed_model(client, license_type="academic")
    response = client.post(
        f"/admin/models/{mid}/license", data={"license_type": "bogus"}, follow_redirects=True
    )
    assert b"Unknown license plan" in response.data
    with client.application.app_context():
        assert db.session.get(Model3D, mid).license_type == "academic"


def test_license_save_says_it_reset_the_access_window(client):
    _make_admin(client)
    mid = _seed_model(client)
    response = client.post(
        f"/admin/models/{mid}/license", data={"license_type": "academic"}, follow_redirects=True
    )
    assert b"Access window reset" in response.data
    with client.application.app_context():
        assert db.session.get(Model3D, mid).license_type == "academic"


def test_health_counts_models_and_conversion_jobs_only(client):
    _make_admin(client)
    failed = _seed_model(client, "m-failed", processing_status="failed", source_format="step")
    gone = _seed_model(client, "m-gone", processing_status="failed", source_format="dicom",
                       file_size=95, storage_limit_bytes=100)
    live_big = _seed_model(client, "m-big", file_size=95, storage_limit_bytes=100)
    now = datetime.now(UTC)
    with client.application.app_context():
        db.session.get(Paper, db.session.get(Model3D, gone).paper_id).status = "deleted"
        for _ in range(3):  # many failed jobs for one failed model
            _job(failed, "model_upload", "failed")
        _job(failed, "poster", "failed")
        _job(live_big, "model_upload", "completed", started_at=now - timedelta(seconds=10), finished_at=now)
        _job(live_big, "poster", "completed", started_at=now - timedelta(seconds=1), finished_at=now)
        db.session.commit()
    html = client.get("/admin/models").get_data(as_text=True)
    assert "<span>STEP</span><strong>1</strong>" in html
    assert "DICOM" not in html  # deleted project's model excluded
    assert "m-big" in html and html.count("Near plan limit") == 1
    assert "10s" in html  # poster job did not drag the average down


def test_force_rebuild_skips_recently_queued_and_deleted_projects(client):
    _make_admin(client)
    ids = [_seed_model(client, f"m-{i}") for i in range(3)]
    gone = _seed_model(client, "m-deleted")
    with client.application.app_context():
        db.session.get(Paper, db.session.get(Model3D, gone).paper_id).status = "deleted"
        _job(ids[0], "poster", "completed", created_at=datetime.now(UTC))
        db.session.commit()
    with patch("app.process_poster_job"):
        client.post("/admin/models/posters/generate-missing", data={"force": "1"})
    with client.application.app_context():
        queued = {j.model_id for j in ConversionJob.query.filter_by(job_type="poster")}
    assert queued == set(ids)  # ids[0] had its own recent job; deleted skipped
    assert gone not in queued
    assert len(queued) == 3
