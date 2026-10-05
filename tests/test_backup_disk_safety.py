"""Backups must never fill the volume: prune first, skip when the disk is
short, delete partial archives, keep fewer local copies when an offsite mirror
exists, leave transient conversion files out, and let an admin delete one."""
import errno
import json
import os
import zipfile
from collections import namedtuple

import pytest

import app as app_module
from models import AuditLog, db
from tests.conftest import create_user, login
from tests.test_admin_panel import _make_admin

Usage = namedtuple("Usage", "total used free")
MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def _own_backup_folder(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "STORAGE_ROOT", str(tmp_path))
    app_module._backup_retry_not_before.clear()
    # Never reach a real offsite mirror from tests.
    monkeypatch.setattr(app_module, "r2_mirror_enabled", lambda: False)
    monkeypatch.setattr(app_module, "mirror_file", lambda *a, **k: None)


def _seed_archives(app, days, folder=None):
    folder = folder or app_module.backup_folder(app)
    names = []
    for day in days:
        name = f"academic_ar_backup_2026010{day}-000000.zip"
        path = os.path.join(folder, name)
        with open(path, "wb") as fh:
            fh.write(b"zip")
        os.utime(path, (1_700_000_000 + day * 86400,) * 2)
        names.append(name)
    return names


def _audit_count(event):
    return AuditLog.query.filter_by(event_type=event).count()


def _short_disk(monkeypatch, free=MIB):
    monkeypatch.setattr(app_module.shutil, "disk_usage", lambda path: Usage(100 * 1024 * MIB, 0, free))


# --- a) prune before create, partial archives removed --------------------------------

def test_prune_runs_before_creating_so_a_full_disk_still_frees_space(app, monkeypatch):
    deleted = []
    monkeypatch.setattr(app_module, "mirror_delete", lambda key: deleted.append(key))
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    _short_disk(monkeypatch)  # archive creation will be skipped
    with app.app_context():
        _seed_archives(app, range(1, 6))
        app.config["BACKUP_RETENTION_COUNT"] = 2
        assert app_module.run_scheduled_backups(app) is None
        left = [b["filename"] for b in app_module.list_backup_archives(app)]
        assert left == ["academic_ar_backup_20260105-000000.zip", "academic_ar_backup_20260104-000000.zip"]
        assert len(deleted) == 3


def test_failed_archive_write_leaves_no_partial_zip(app, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(app_module, "add_folder_to_zip", boom)
    with app.app_context():
        with pytest.raises(OSError):
            app_module.create_backup_archive(app)
        assert [f for f in os.listdir(app_module.backup_folder(app)) if f.endswith(".zip")] == []
        assert app_module.run_scheduled_backups(app) is None
        assert [f for f in os.listdir(app_module.backup_folder(app)) if f.endswith(".zip")] == []
        assert _audit_count("admin_backup_failed") == 1


def test_a_failed_daily_backup_is_not_retried_every_minute(app, monkeypatch):
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(app_module, "add_folder_to_zip", boom)
    with app.app_context():
        app_module.run_scheduled_backups(app)
        app_module.run_scheduled_backups(app)
        assert len(calls) == 1
        app_module._backup_retry_not_before.clear()  # an hour later
        app_module.run_scheduled_backups(app)
        assert len(calls) == 2


# --- b) skip when the archive would not fit -----------------------------------------

def test_low_disk_skips_the_archive_and_audits_it(app, monkeypatch):
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    _short_disk(monkeypatch)
    with app.app_context():
        with pytest.raises(app_module.BackupSkippedLowDisk):
            app_module.create_backup_archive(app)
        assert [f for f in os.listdir(app_module.backup_folder(app)) if f.endswith(".zip")] == []
        row = AuditLog.query.filter_by(event_type="admin_backup_skipped_low_disk").one()
        for key in ("estimated_bytes", "free_bytes", "min_free_bytes", "required_bytes"):
            assert key in row.details
        assert row.details["free_bytes"] == MIB
        assert row.details["min_free_bytes"] == 512 * MIB


def test_enough_space_still_creates_the_archive(app, monkeypatch):
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    monkeypatch.setattr(app_module.shutil, "disk_usage", lambda path: Usage(10**13, 0, 10**12))
    with app.app_context():
        assert app_module.run_scheduled_backups(app)


def test_estimate_counts_what_the_archive_includes(app):
    with app.app_context():
        baseline = app_module.estimate_backup_size(app)  # the SQLite file
        for key, name in (("UPLOAD_FOLDER", "a.bin"), ("CONVERTED_FOLDER", "model.glb")):
            os.makedirs(app.config[key], exist_ok=True)
            with open(os.path.join(app.config[key], name), "wb") as fh:
                fh.write(b"x" * 1000)
        # transient files do not count
        with open(os.path.join(app.config["CONVERTED_FOLDER"], "m.step-tmp.glb"), "wb") as fh:
            fh.write(b"x" * 5000)
        assert app_module.estimate_backup_size(app) == baseline + 2000


def test_skipped_daily_backup_is_not_retried_within_the_hour(app, monkeypatch):
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    _short_disk(monkeypatch)
    with app.app_context():
        assert app_module.run_scheduled_backups(app) is None
        assert app_module.run_scheduled_backups(app) is None
        assert _audit_count("admin_backup_skipped_low_disk") == 1
        app_module._backup_retry_not_before.clear()  # an hour later
        app_module.run_scheduled_backups(app)
        assert _audit_count("admin_backup_skipped_low_disk") == 2


def test_skipped_manual_request_is_answered(app, monkeypatch):
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    _short_disk(monkeypatch)
    with app.app_context():
        app_module.log_audit("admin_backup_requested")
        assert app_module.pending_backup_request() is not None
        assert app_module.run_scheduled_backups(app) is None
        assert app_module.pending_backup_request() is None
        assert _audit_count("admin_backup_skipped_low_disk") == 1


# --- c) local vs offsite retention ----------------------------------------------------

def test_local_retention_defaults(app, monkeypatch):
    app.config["BACKUP_RETENTION_COUNT"] = 14
    app.config["BACKUP_LOCAL_RETENTION_COUNT"] = None
    assert app_module.backup_local_retention(app) == 14
    monkeypatch.setattr(app_module, "r2_mirror_enabled", lambda: True)
    assert app_module.backup_local_retention(app) == 2
    app.config["BACKUP_LOCAL_RETENTION_COUNT"] = 3
    assert app_module.backup_local_retention(app) == 3
    monkeypatch.setattr(app_module, "r2_mirror_enabled", lambda: False)
    assert app_module.backup_local_retention(app) == 3


def test_mirror_keeps_more_than_the_volume(app, monkeypatch):
    deleted = []
    monkeypatch.setattr(app_module, "mirror_delete", lambda key: deleted.append(key))
    monkeypatch.setattr(app_module, "r2_mirror_enabled", lambda: True)
    app.config["BACKUP_RETENTION_COUNT"] = 4
    app.config["BACKUP_LOCAL_RETENTION_COUNT"] = None  # -> 2 with a mirror
    with app.app_context():
        _seed_archives(app, range(1, 6))
        app_module.prune_backup_archives(app)
        local = [b["filename"] for b in app_module.list_backup_archives(app)]
        assert local == ["academic_ar_backup_20260105-000000.zip", "academic_ar_backup_20260104-000000.zip"]
        # only the 5th copy is beyond the offsite retention of 4
        assert deleted == ["admin_backups/academic_ar_backup_20260101-000000.zip"]
        index = json.loads(open(os.path.join(app_module.backup_folder(app), "backup_index.json")).read())
        assert [i["filename"] for i in index["archives"]] == [
            f"academic_ar_backup_2026010{d}-000000.zip" for d in (5, 4, 3, 2)
        ]
        assert set(index["archives"][0]) == {"filename", "created_at"}
        # a newer archive pushes a name that is no longer on disk out of the mirror
        deleted.clear()
        _seed_archives(app, [6])
        app_module._record_backup_in_index(app, "academic_ar_backup_20260106-000000.zip")
        app_module.prune_backup_archives(app)
        assert deleted == ["admin_backups/academic_ar_backup_20260102-000000.zip"]
        assert len(app_module.list_backup_archives(app)) == 2


def test_default_case_keeps_the_same_number_locally_and_offsite(app, monkeypatch):
    deleted = []
    monkeypatch.setattr(app_module, "mirror_delete", lambda key: deleted.append(key))
    app.config["BACKUP_RETENTION_COUNT"] = 3
    app.config["BACKUP_LOCAL_RETENTION_COUNT"] = None
    with app.app_context():
        _seed_archives(app, range(1, 6))
        removed = app_module.prune_backup_archives(app)
        assert sorted(removed) == [f"academic_ar_backup_2026010{d}-000000.zip" for d in (1, 2)]
        assert len(app_module.list_backup_archives(app)) == 3
        assert len(deleted) == 2


# --- d) transient files are not archived -------------------------------------------

def test_transient_files_are_left_out_of_the_archive(app):
    with app.app_context():
        converted = os.path.join(app.config["CONVERTED_FOLDER"], "m1")
        uploads = app.config["UPLOAD_FOLDER"]
        files = {
            os.path.join(converted, "model.glb"): True,
            os.path.join(converted, "model.glb.optimized.glb"): False,
            os.path.join(converted, "model.glb.step-tmp.glb"): False,
            os.path.join(converted, ".medical_result_ab12", "volume.nii"): False,
            os.path.join(converted, ".medical_zz", "deep", "slice.dcm"): False,
            os.path.join(uploads, "m1", "v1", "part.stl"): True,
            os.path.join(uploads, "_replace_abc", "raw.stl"): False,
        }
        for path in files:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"data")
        filename = app_module.create_backup_archive(app)
        with zipfile.ZipFile(os.path.join(app_module.backup_folder(app), filename)) as zf:
            names = set(zf.namelist())
        assert "converted/m1/model.glb" in names
        assert "uploads/m1/v1/part.stl" in names
        assert not [n for n in names if ".medical" in n or "_replace_" in n or n.endswith((".optimized.glb", ".step-tmp.glb"))]
        with zipfile.ZipFile(os.path.join(app_module.backup_folder(app), filename)) as zf:
            assert "converted_files=1" in zf.read("manifest.txt").decode()


def test_medical_staging_lives_outside_the_archived_folders(app):
    staging = os.path.abspath(app.config["MEDICAL_STAGING_FOLDER"])
    for key in ("UPLOAD_FOLDER", "CONVERTED_FOLDER", "QR_FOLDER", "PDF_FOLDER"):
        root = os.path.abspath(app.config[key])
        assert not staging.startswith(root + os.sep) and staging != root


# --- 4) admin delete -----------------------------------------------------------------

def test_admin_can_delete_an_archive(client, app, monkeypatch):
    deleted = []
    monkeypatch.setattr(app_module, "mirror_delete", lambda key: deleted.append(key))
    _make_admin(client)
    with app.app_context():
        names = _seed_archives(app, [1, 2])
        folder = app_module.backup_folder(app)
        app_module._record_backup_in_index(app, names[0])
    response = client.post(f"/admin/backups/{names[0]}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert names[1] in response.get_data(as_text=True)
    assert not os.path.exists(os.path.join(folder, names[0]))
    assert os.path.exists(os.path.join(folder, names[1]))
    assert deleted == [f"admin_backups/{names[0]}"]
    with app.app_context():
        row = AuditLog.query.filter_by(event_type="admin_backup_deleted").one()
        assert row.resource_id == names[0]
        index = json.loads(open(os.path.join(folder, "backup_index.json")).read())
        assert names[0] not in [i["filename"] for i in index["archives"]]


def test_non_admin_cannot_delete_an_archive(client, app):
    with app.app_context():
        create_user()
        names = _seed_archives(app, [1])
        folder = app_module.backup_folder(app)
    login(client)
    assert client.post(f"/admin/backups/{names[0]}/delete").status_code == 403
    assert os.path.exists(os.path.join(folder, names[0]))


def test_anonymous_cannot_delete_an_archive(client, app):
    with app.app_context():
        names = _seed_archives(app, [1])
        folder = app_module.backup_folder(app)
    assert client.post(f"/admin/backups/{names[0]}/delete").status_code in (302, 401, 403)
    assert os.path.exists(os.path.join(folder, names[0]))


@pytest.mark.parametrize(
    "name",
    [
        "..%2F..%2Fetc%2Fpasswd",
        "%2e%2e%2fsecret.zip",
        "..",
        "backup_index.json",
        "..%5Cevil.zip",
        "academic_ar_backup_20990101-000000.zip",  # does not exist
    ],
)
def test_delete_rejects_traversal_and_foreign_names(client, app, name):
    _make_admin(client)
    with app.app_context():
        names = _seed_archives(app, [1])
        folder = app_module.backup_folder(app)
        app_module._record_backup_in_index(app, names[0])
    outside = os.path.join(os.path.dirname(folder), "secret.zip")
    with open(outside, "wb") as fh:
        fh.write(b"keep")
    response = client.post(f"/admin/backups/{name}/delete")
    assert response.status_code in (400, 404)
    assert os.path.exists(outside)
    assert os.path.exists(os.path.join(folder, names[0]))
    assert os.path.exists(os.path.join(folder, "backup_index.json"))
    with app.app_context():
        assert _audit_count("admin_backup_deleted") == 0


def test_delete_needs_post_and_csrf(client, app):
    _make_admin(client)
    with app.app_context():
        names = _seed_archives(app, [1])
        folder = app_module.backup_folder(app)
    assert client.get(f"/admin/backups/{names[0]}/delete").status_code in (404, 405)
    app.config["WTF_CSRF_ENABLED"] = True
    client.post(f"/admin/backups/{names[0]}/delete")
    assert os.path.exists(os.path.join(folder, names[0]))


def test_backups_page_has_a_confirmed_delete_button(client, app):
    _make_admin(client)
    with app.app_context():
        names = _seed_archives(app, [1])
    html = client.get("/admin/backups").get_data(as_text=True)
    assert f"/admin/backups/{names[0]}/delete" in html
    assert "confirm(" in html
