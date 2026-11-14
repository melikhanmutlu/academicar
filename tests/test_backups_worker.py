"""Backups are built by the worker, never inside a production web request,
and old archives are pruned."""
import os

import pytest

from models import AuditLog, db
from tests.test_admin_panel import _make_admin


@pytest.fixture(autouse=True)
def _own_backup_folder(app, tmp_path, monkeypatch):
    """The shared test storage root accumulates archives across runs."""
    monkeypatch.setitem(app.config, "STORAGE_ROOT", str(tmp_path))


def _production_like(app, monkeypatch):
    monkeypatch.setitem(app.config, "TESTING", False)
    monkeypatch.setitem(app.config, "DEV_INLINE_JOBS", False)


def test_create_backup_now_only_queues_a_request(client, app, monkeypatch):
    from app import list_backup_archives, run_scheduled_backups

    _make_admin(client)
    _production_like(app, monkeypatch)
    html = client.post("/admin/backups/create", follow_redirects=True).get_data(as_text=True)
    assert "Backup requested" in html
    assert "the worker is building it" in html
    with app.app_context():
        assert list_backup_archives(app) == []
        assert AuditLog.query.filter_by(event_type="admin_backup_requested").count() == 1
        # A second click while pending does not queue another request.
    client.post("/admin/backups/create")
    with app.app_context():
        assert AuditLog.query.filter_by(event_type="admin_backup_requested").count() == 1

        filename = run_scheduled_backups(app)
        assert filename and filename in [b["filename"] for b in list_backup_archives(app)]
        from app import pending_backup_request

        assert pending_backup_request() is None


def test_opening_the_backups_page_no_longer_builds_an_archive(client, app, monkeypatch):
    from app import list_backup_archives

    _make_admin(client)
    _production_like(app, monkeypatch)
    assert client.get("/admin/backups").status_code == 200
    with app.app_context():
        assert list_backup_archives(app) == []


def test_worker_makes_one_daily_archive(app):
    from app import list_backup_archives, run_scheduled_backups

    with app.app_context():
        assert run_scheduled_backups(app)
        assert run_scheduled_backups(app) is None  # already have today's
        assert len(list_backup_archives(app)) == 1


def test_prune_keeps_the_newest_archives(app, monkeypatch):
    import app as app_module
    from app import backup_folder, list_backup_archives, prune_backup_archives

    deleted = []
    monkeypatch.setattr(app_module, "mirror_delete", lambda key: deleted.append(key))
    with app.app_context():
        folder = backup_folder(app)
        for day in range(1, 6):
            path = os.path.join(folder, f"academic_ar_backup_2026010{day}-000000.zip")
            with open(path, "wb") as fh:
                fh.write(b"zip")
            os.utime(path, (1_700_000_000 + day * 86400,) * 2)
        removed = prune_backup_archives(app, keep=2)
        assert sorted(removed) == [f"academic_ar_backup_2026010{d}-000000.zip" for d in (1, 2, 3)]
        assert [b["filename"] for b in list_backup_archives(app)] == [
            "academic_ar_backup_20260105-000000.zip",
            "academic_ar_backup_20260104-000000.zip",
        ]
        assert deleted == [f"admin_backups/{name}" for name in removed]


def _fake_archives(app, days):
    from app import backup_folder

    folder = backup_folder(app)
    names = []
    for day in days:
        name = f"academic_ar_backup_2026010{day}-000000.zip"
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(b"zip")
        os.utime(path, (1_700_000_000 + day * 86400,) * 2)
        names.append(name)
    return names


def test_backup_that_failed_to_mirror_is_kept_locally_and_retried(app, monkeypatch):
    """A failed offsite upload must not leave the archive nowhere: it stays on
    the volume past the local retention and the worker uploads it again."""
    import app as app_module
    from app import (
        backup_folder,
        list_backup_archives,
        mirror_backup_archive,
        prune_backup_archives,
        retry_unmirrored_backups,
        unmirrored_backup_archives,
    )

    uploads = []
    result = {"ok": False}

    def fake_upload(path, key):
        uploads.append(key)
        return result["ok"]

    monkeypatch.setattr(app_module, "r2_mirror_enabled", lambda: True)
    monkeypatch.setattr(app_module, "mirror_file_sync", fake_upload)
    monkeypatch.setattr(app_module, "mirror_delete", lambda key: None)
    monkeypatch.setattr(app_module, "BACKUP_MIRROR_ATTEMPTS", 2)
    monkeypatch.setattr(app_module.time, "sleep", lambda seconds: None)
    with app.app_context():
        names = _fake_archives(app, range(1, 5))
        for name in names:
            assert mirror_backup_archive(app, name) is False
        assert len(uploads) == 8  # two attempts each
        assert sorted(unmirrored_backup_archives(app)) == names
        assert AuditLog.query.filter_by(event_type="admin_backup_mirror_failed").count() == 4

        # Local retention is 1, but nothing unmirrored may be deleted.
        prune_backup_archives(app, keep=14, local_keep=1)
        assert len(list_backup_archives(app)) == 4

        result["ok"] = True
        assert retry_unmirrored_backups(app) == 4
        assert unmirrored_backup_archives(app) == []
        prune_backup_archives(app, keep=14, local_keep=1)
        assert [b["filename"] for b in list_backup_archives(app)] == [names[-1]]
        assert not [f for f in os.listdir(backup_folder(app)) if f.endswith(".unmirrored")]


def test_backup_mirror_alert_is_sent_once_a_day(app, client, monkeypatch):
    import app as app_module
    from app import mirror_backup_archive
    from ops_alerts import send_backup_mirror_failure_alert

    sent = []
    monkeypatch.setattr("utils.email.send_email", lambda *args, **kwargs: sent.append(args) or True)
    monkeypatch.setattr(app_module, "r2_mirror_enabled", lambda: True)
    monkeypatch.setattr(app_module, "mirror_file_sync", lambda path, key: False)
    monkeypatch.setattr(app_module, "BACKUP_MIRROR_ATTEMPTS", 1)
    _make_admin(client)
    with app.app_context():
        assert send_backup_mirror_failure_alert() == 0  # nothing pending
        (name,) = _fake_archives(app, [1])
        mirror_backup_archive(app, name)
        assert send_backup_mirror_failure_alert() == 1
        assert len(sent) >= 1
        count = len(sent)
        assert send_backup_mirror_failure_alert() == 0  # deduped for 24 h
        assert len(sent) == count
