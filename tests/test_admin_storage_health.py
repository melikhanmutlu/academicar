"""Admin storage page shows the volume, /health reports disk without failing,
and the worker warns (at most hourly) when space is short."""
import logging
import os
from collections import namedtuple

import app as app_module
from tests.test_admin_panel import _make_admin

Usage = namedtuple("Usage", "total used free")
GIB = 1024**3
MIB = 1024**2


def _disk(monkeypatch, total, free):
    monkeypatch.setattr(app_module.shutil, "disk_usage", lambda path: Usage(total, total - free, free))


def test_storage_page_shows_disk_numbers(client, app, tmp_path, monkeypatch):
    app.config["STORAGE_ROOT"] = str(tmp_path)
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    os.makedirs(app.config["MEDICAL_STAGING_FOLDER"], exist_ok=True)
    with open(os.path.join(app.config["MEDICAL_STAGING_FOLDER"], "scan.zip"), "wb") as fh:
        fh.write(b"x" * 3000)
    os.makedirs(tmp_path / "admin_backups", exist_ok=True)
    (tmp_path / "admin_backups" / "academic_ar_backup_20260101-000000.zip").write_bytes(b"y" * 5000)
    _disk(monkeypatch, 50 * GIB, 30 * GIB)
    _make_admin(client)
    html = client.get("/admin/storage").get_data(as_text=True)
    assert "Volume" in html
    assert "50.0 GB" in html and "30.0 GB" in html and "20.0 GB" in html  # total / free / used
    assert "Backups" in html and app_module.format_file_size(5000) in html
    assert "Medical staging" in html and app_module.format_file_size(3000) in html
    assert "Storage is nearly full" not in html


def test_storage_page_warns_when_free_space_is_under_ten_percent(client, app, tmp_path, monkeypatch):
    app.config["STORAGE_ROOT"] = str(tmp_path)
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    _disk(monkeypatch, 50 * GIB, 4 * GIB)
    _make_admin(client)
    html = client.get("/admin/storage").get_data(as_text=True)
    assert "Storage is nearly full" in html


def test_storage_page_warns_when_free_space_is_under_the_minimum(client, app, tmp_path, monkeypatch):
    app.config["STORAGE_ROOT"] = str(tmp_path)
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    _disk(monkeypatch, 1000 * GIB, 100 * MIB)
    _make_admin(client)
    assert "Storage is nearly full" in client.get("/admin/storage").get_data(as_text=True)


def test_health_reports_disk_and_stays_200_when_low(client, app, tmp_path, monkeypatch):
    app.config["STORAGE_ROOT"] = str(tmp_path)
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    _disk(monkeypatch, 50 * GIB, 100 * MIB)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["disk"]["free_bytes"] == 100 * MIB
    assert body["disk"]["low_disk"] is True
    _disk(monkeypatch, 50 * GIB, 30 * GIB)
    body = client.get("/health").get_json()
    assert body["status"] == "ok" and body["disk"]["low_disk"] is False


def test_health_survives_an_unreadable_disk(client, monkeypatch):
    def boom(path):
        raise OSError("gone")

    monkeypatch.setattr(app_module.shutil, "disk_usage", boom)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_worker_warns_only_when_below_the_minimum(app, tmp_path, monkeypatch, caplog):
    app.config["STORAGE_ROOT"] = str(tmp_path)
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    _disk(monkeypatch, 1000 * GIB, 100 * MIB)
    with caplog.at_level(logging.WARNING):
        assert app_module.warn_if_storage_low(app) is True
    assert any("Storage volume is low" in r.getMessage() for r in caplog.records)
    _disk(monkeypatch, 1000 * GIB, 50 * GIB)
    assert app_module.warn_if_storage_low(app) is False
