"""Error monitoring and uptime endpoints."""
from datetime import UTC, datetime, timedelta

from models import ConversionJob, db
from tests.test_admin_panel import _seed_job


def test_sentry_stays_off_without_dsn(monkeypatch):
    import services.monitoring as monitoring

    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(monitoring, "_initialised", False)
    assert monitoring.init_error_monitoring("production") is False


def test_sentry_initialises_with_dsn_and_without_pii(monkeypatch):
    import sentry_sdk

    import services.monitoring as monitoring

    seen = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: seen.update(kw))
    monkeypatch.setattr(monitoring, "_initialised", False)
    monkeypatch.setenv("SENTRY_DSN", "https://key@o0.ingest.sentry.io/1")
    assert monitoring.init_error_monitoring("production") is True
    assert seen["environment"] == "production"
    assert seen["send_default_pii"] is False
    assert seen["traces_sample_rate"] == 0


def test_worker_health_ok_when_queue_is_moving(client):
    response = client.get("/health/worker")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_worker_health_503_when_jobs_wait_too_long(client):
    job_id = _seed_job(client, status="pending", attempts=0)
    with client.application.app_context():
        job = db.session.get(ConversionJob, job_id)
        job.created_at = datetime.now(UTC) - timedelta(hours=2)
        db.session.commit()
    response = client.get("/health/worker")
    assert response.status_code == 503
    assert response.get_json()["status"] == "stalled"
