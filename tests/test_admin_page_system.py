from datetime import UTC, datetime, timedelta

from models import ConversionJob, db
from tests.conftest import create_user, login
from tests.test_admin_enrichment import _seed_model


def _admin(client):
    with client.application.app_context():
        user = create_user(email="admin@example.com", username="Admin User")
        user.is_admin = True
        db.session.commit()
    login(client, email="admin@example.com")


def _health(monkeypatch, mode):
    import app as app_module

    monkeypatch.setattr(
        app_module,
        "_describe_cli_resolution",
        lambda command: {"available": mode == "local", "mode": mode, "detail": "x"},
    )


def test_npx_tools_are_not_reported_missing(client, monkeypatch):
    _health(monkeypatch, "npx")
    _admin(client)
    text = client.get("/admin/system").get_data(as_text=True)
    assert "Via npx" in text
    assert "External conversion tools" not in text


def test_missing_tool_shows_warning_chip(client, monkeypatch):
    _health(monkeypatch, "missing")
    _admin(client)
    assert 'status-chip is-failed">Missing' in client.get("/admin/system").get_data(as_text=True)


def test_stalled_worker_is_flagged(client):
    _admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        db.session.add(
            ConversionJob(
                model_id=model_id,
                job_type="conversion",
                status="pending",
                created_at=datetime.now(UTC) - timedelta(hours=3),
            )
        )
        db.session.commit()
    assert "Worker stalled" in client.get("/admin/system").get_data(as_text=True)


def test_failed_ar_doctor_shows_chip_without_raw_json(client):
    _admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        db.session.add(
            ConversionJob(
                model_id=model_id,
                job_type="ar_doctor",
                status="completed",
                payload={"report": {"conversion_ok": False, "conversion_stderr": "boom", "blender_python": ["a"]}},
            )
        )
        db.session.commit()
    text = client.get("/admin/system").get_data(as_text=True)
    assert 'status-chip is-failed">Failed' in text
    assert "boom" in text and "Details" in text
    assert "blender_python" not in text
