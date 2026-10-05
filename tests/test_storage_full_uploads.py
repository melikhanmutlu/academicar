"""A full storage volume must give people a friendly message, never a 500, and
leave no half-written directories or Model3D rows behind."""
import errno
import logging
import os
from collections import namedtuple

import pytest
from werkzeug.datastructures import FileStorage

import app as app_module
from models import ConversionJob, Model3D, Paper, db
from services.storage_service import (
    STORAGE_FULL_MESSAGE,
    StorageFullError,
    safe_save_file,
    upload_space_shortfall,
)
from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

Usage = namedtuple("Usage", "total used free")
MIB = 1024 * 1024


def _project(client, title="Space Project"):
    register(client)
    client.post("/papers/new", data={"title": title}, follow_redirects=True)
    with client.application.app_context():
        return Paper.query.filter_by(title=title).one().slug


def _upload(client, slug):
    return client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": upload_file_bytes(valid_ascii_stl_bytes(), "part.stl"),
            "compliance_confirm": "yes",
            "source_unit": "mm",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _replace(client, model_id):
    return client.post(
        f"/models/{model_id}/replace",
        data={
            "file": upload_file_bytes(valid_ascii_stl_bytes(), "part2.stl"),
            "compliance_confirm": "yes",
            "source_unit": "mm",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def _no_leftovers(app):
    for key in ("UPLOAD_FOLDER", "CONVERTED_FOLDER", "MEDICAL_STAGING_FOLDER"):
        folder = app.config[key]
        assert not os.path.isdir(folder) or os.listdir(folder) == [], (key, os.listdir(folder))


@pytest.fixture()
def low_disk(app, monkeypatch):
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    monkeypatch.setattr(app_module.shutil, "disk_usage", lambda path: Usage(100 * 1024 * MIB, 99 * 1024 * MIB, 1 * MIB))


def test_shortfall_uses_factor_2_and_4_plus_reserve(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: Usage(10**12, 0, 1000 + 2 * 100))
    assert upload_space_shortfall(str(tmp_path), 100, medical=False, min_free=1000) is None
    assert upload_space_shortfall(str(tmp_path), 101, medical=False, min_free=1000)["required"] == 1202
    monkeypatch.setattr("shutil.disk_usage", lambda path: Usage(10**12, 0, 1000 + 3 * 100))
    shortfall = upload_space_shortfall(str(tmp_path), 100, medical=True, min_free=1000)
    assert shortfall["required"] == 1400 and shortfall["free"] == 1300
    # No Content-Length: only the reserve is enforced.
    assert upload_space_shortfall(str(tmp_path), None, medical=False, min_free=1000) is None


def test_upload_with_low_free_space_is_refused_with_message(client, app, low_disk, caplog):
    slug = _project(client)
    with caplog.at_level(logging.ERROR):
        response = _upload(client, slug)
    assert response.status_code == 200
    assert STORAGE_FULL_MESSAGE in response.get_data(as_text=True)
    with app.app_context():
        assert Model3D.query.count() == 0
        assert ConversionJob.query.count() == 0
    _no_leftovers(app)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "free" in r.getMessage()]
    assert errors, "low free space must be logged at ERROR (Sentry)"


def test_upload_with_enough_space_still_works(client, app, monkeypatch):
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    monkeypatch.setattr(app_module.shutil, "disk_usage", lambda path: Usage(10**13, 0, 10**12))
    slug = _project(client)
    _upload(client, slug)
    with app.app_context():
        assert Model3D.query.count() == 1


def test_enospc_creating_the_converted_dir_cleans_up(client, app, monkeypatch):
    real_makedirs = os.makedirs
    converted_root = app.config["CONVERTED_FOLDER"]

    def fake_makedirs(path, *args, **kwargs):
        if str(path).startswith(converted_root) and str(path) != converted_root:
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        return real_makedirs(path, *args, **kwargs)

    slug = _project(client)
    monkeypatch.setattr(app_module.os, "makedirs", fake_makedirs)
    response = _upload(client, slug)
    monkeypatch.undo()
    assert response.status_code == 200
    assert STORAGE_FULL_MESSAGE in response.get_data(as_text=True)
    with app.app_context():
        assert Model3D.query.count() == 0
    _no_leftovers(app)


def test_enospc_creating_the_upload_dir_is_friendly(client, app, monkeypatch):
    real_makedirs = os.makedirs
    upload_root = app.config["UPLOAD_FOLDER"]

    def fake_makedirs(path, *args, **kwargs):
        if str(path).startswith(upload_root) and str(path) != upload_root:
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        return real_makedirs(path, *args, **kwargs)

    slug = _project(client)
    monkeypatch.setattr(app_module.os, "makedirs", fake_makedirs)
    response = _upload(client, slug)
    monkeypatch.undo()
    assert response.status_code == 200
    assert STORAGE_FULL_MESSAGE in response.get_data(as_text=True)
    _no_leftovers(app)


def test_enospc_from_raw_oserror_in_safe_save_file_is_friendly(client, app, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    slug = _project(client)
    monkeypatch.setattr(app_module, "safe_save_file", boom)
    response = _upload(client, slug)
    monkeypatch.undo()
    assert STORAGE_FULL_MESSAGE in response.get_data(as_text=True)
    with app.app_context():
        assert Model3D.query.count() == 0
    _no_leftovers(app)


def test_enospc_while_writing_the_file_is_friendly(client, app, monkeypatch):
    def boom(self, dst, buffer_size=16384):
        raise OSError(errno.ENOSPC, "No space left on device")

    slug = _project(client)
    monkeypatch.setattr(FileStorage, "save", boom)
    response = _upload(client, slug)
    monkeypatch.undo()
    assert STORAGE_FULL_MESSAGE in response.get_data(as_text=True)
    with app.app_context():
        assert Model3D.query.count() == 0
    _no_leftovers(app)


def test_enospc_while_archiving_the_source_cleans_up(client, app, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    slug = _project(client)
    monkeypatch.setattr(app_module.shutil, "copy2", boom)
    response = _upload(client, slug)
    monkeypatch.undo()
    assert STORAGE_FULL_MESSAGE in response.get_data(as_text=True)
    with app.app_context():
        assert Model3D.query.count() == 0
    _no_leftovers(app)


def test_safe_save_file_raises_storage_full_error_on_enospc(tmp_path):
    class Full:
        def save(self, dest):
            raise OSError(errno.ENOSPC, "No space left on device")

    with pytest.raises(StorageFullError) as excinfo:
        safe_save_file(Full(), str(tmp_path / "a" / "b.bin"))
    assert str(excinfo.value) == STORAGE_FULL_MESSAGE


def test_other_oserrors_keep_the_generic_storage_message(tmp_path):
    class Denied:
        def save(self, dest):
            raise PermissionError(errno.EACCES, "denied")

    from services.storage_service import StorageError

    with pytest.raises(StorageError) as excinfo:
        safe_save_file(Denied(), str(tmp_path / "b.bin"))
    assert not isinstance(excinfo.value, StorageFullError)


def _make_model(client, app):
    slug = _project(client)
    _upload(client, slug)
    with app.app_context():
        model = Model3D.query.one()
        model.license_type = "academic"
        db.session.commit()
        return model.id


def test_replace_with_low_free_space_is_refused_and_keeps_the_model(client, app, monkeypatch):
    model_id = _make_model(client, app)
    with app.app_context():
        before = sorted(os.listdir(app.config["UPLOAD_FOLDER"]))
        version = db.session.get(Model3D, model_id).version
    app.config["STORAGE_MIN_FREE_BYTES"] = 512 * MIB
    monkeypatch.setattr(app_module.shutil, "disk_usage", lambda path: Usage(100 * 1024 * MIB, 99 * 1024 * MIB, MIB))
    response = _replace(client, model_id)
    assert response.status_code == 200
    assert STORAGE_FULL_MESSAGE in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Model3D, model_id).version == version
        assert sorted(os.listdir(app.config["UPLOAD_FOLDER"])) == before


def test_replace_enospc_on_makedirs_is_friendly(client, app, monkeypatch):
    model_id = _make_model(client, app)
    real_makedirs = os.makedirs

    def fake_makedirs(path, *args, **kwargs):
        if "_replace_" in str(path):
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        return real_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(app_module.os, "makedirs", fake_makedirs)
    response = _replace(client, model_id)
    monkeypatch.undo()
    assert STORAGE_FULL_MESSAGE in response.get_data(as_text=True)
    with app.app_context():
        assert not [n for n in os.listdir(app.config["UPLOAD_FOLDER"]) if n.startswith("_replace_")]


def test_replace_enospc_while_archiving_cleans_up(client, app, monkeypatch):
    model_id = _make_model(client, app)

    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    with app.app_context():
        version = db.session.get(Model3D, model_id).version
    monkeypatch.setattr(app_module, "archive_source_file", boom)
    response = _replace(client, model_id)
    monkeypatch.undo()
    assert STORAGE_FULL_MESSAGE in response.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(Model3D, model_id).version == version
        assert not [n for n in os.listdir(app.config["UPLOAD_FOLDER"]) if n.startswith("_replace_")]
