"""A full storage volume must not stop the app from booting."""
import errno
import os
from pathlib import Path
from uuid import uuid4

from app import create_app


def test_app_boots_when_a_storage_folder_cannot_be_created(monkeypatch):
    base = (Path("tests_runtime") / uuid4().hex).resolve()
    staging = str(base / "medical_staging")
    real_makedirs = os.makedirs

    def full_disk(path, *args, **kwargs):
        if str(path) == staging:
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        return real_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(os, "makedirs", full_disk)
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{base / 'test.db'}",
        "UPLOAD_FOLDER": str(base / "uploads"),
        "CONVERTED_FOLDER": str(base / "converted"),
        "QR_FOLDER": str(base / "qr"),
        "PDF_FOLDER": str(base / "pdfs"),
        "BLOG_IMAGE_FOLDER": str(base / "blog"),
        "INSTITUTION_LOGO_FOLDER": str(base / "logos"),
        "MEDICAL_STAGING_FOLDER": staging,
    })
    assert app is not None
    assert not os.path.exists(staging)
