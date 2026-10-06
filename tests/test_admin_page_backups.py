import os

import pytest

from models import db
from tests.test_admin_panel import _make_admin


@pytest.fixture(autouse=True)
def _own_backup_folder(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "STORAGE_ROOT", str(tmp_path))


def _seed(app, n):
    import app as app_module

    folder = app_module.backup_folder(app)
    for i in range(n):
        with open(os.path.join(folder, f"academic_ar_backup_2026090{i + 1}_000000.zip"), "wb") as handle:
            handle.write(b"x")


def _page(client):
    return client.get("/admin/backups").get_data(as_text=True)


def test_no_offsite_claim_when_mirror_disabled_and_singular_count(client, app, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "r2_mirror_enabled", lambda: False)
    monkeypatch.setitem(app.config, "BACKUP_LOCAL_RETENTION_COUNT", 20)
    _make_admin(client)
    with app.app_context():
        _seed(app, 1)
    html = _page(client)
    assert "offsite" not in html.lower()
    assert "1 archive<" in html and "1 archives" not in html
    assert "Last backup:" in html
    assert "Keeps the newest 14" in html


def test_offsite_line_when_mirror_enabled(client, app, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "r2_mirror_enabled", lambda: True)
    _make_admin(client)
    html = _page(client)
    assert "14 offsite and 2 on this server" in html


def test_pending_request_hides_stale_failure_note(client, app):
    from datetime import UTC, datetime, timedelta

    from models import AuditLog

    _make_admin(client)
    now = datetime.now(UTC).replace(tzinfo=None)
    with app.app_context():
        db.session.add(
            AuditLog(event_type="admin_backup_failed", details={"error": "boom"}, timestamp=now - timedelta(hours=1))
        )
        db.session.add(AuditLog(event_type="admin_backup_requested", details={}, timestamp=now))
        db.session.commit()
    html = _page(client)
    assert "the worker is building it" in html
    assert "Last backup failed" not in html
