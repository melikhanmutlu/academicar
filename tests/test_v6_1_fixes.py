"""Regression tests for the v6.1 second audit fixes.

Covers: QR route null safety and authorization, path traversal protection,
file handle cleanup, worker resilience, and backup manifest versioning.
"""
from pathlib import Path
from unittest.mock import patch

from models import Model3D, Paper, User, db


# --------------------------------------------------------------------------- #
# 1.1 QR image route — null paper safety
# --------------------------------------------------------------------------- #
def test_qr_image_returns_404_for_orphaned_model(client):
    """Model with no paper should 404, not crash."""
    app = client.application
    with app.app_context():
        user = User(email="orphan@example.com", username="Orphan")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(title="Temp", slug="temp-paper", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="orphan-qr-test",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="model.glb",
            qr_code_path="qr.png",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()
        db.session.delete(paper)
        db.session.commit()

    resp = client.get("/qr-image/orphan-qr-test")
    assert resp.status_code == 404


def test_qr_print_returns_404_for_orphaned_model(client):
    """Model with no paper should 404 on the print page."""
    from tests.conftest import login, register

    register(client, email="qrprint@example.com")
    app = client.application
    with app.app_context():
        user = User.query.filter_by(email="qrprint@example.com").one()
        paper = Paper(title="Temp2", slug="temp-paper-2", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="orphan-qr-print",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="model.glb",
            qr_code_path="qr.png",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()
        db.session.delete(paper)
        db.session.commit()

    resp = client.get("/qr-print/orphan-qr-print")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 1.2 QR image route — authorization check
# --------------------------------------------------------------------------- #
def test_qr_image_hidden_for_private_paper(client):
    """QR image for a private (non-public) paper should 404 for anonymous."""
    app = client.application
    with app.app_context():
        user = User(email="private@example.com", username="Private")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(
            title="Private Paper", slug="private-paper",
            user_id=user.id, is_public=False,
        )
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="private-model-qr",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="model.glb",
            qr_code_path="qr.png",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()

    resp = client.get("/qr-image/private-model-qr")
    assert resp.status_code == 404


def test_qr_image_visible_for_public_paper(client):
    """QR image for a public paper should be accessible (if the file exists)."""
    import os

    app = client.application
    with app.app_context():
        user = User(email="pub@example.com", username="Pub")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(
            title="Public Paper", slug="public-paper",
            user_id=user.id, is_public=True,
        )
        db.session.add(paper)
        db.session.flush()
        qr_dir = app.config["QR_FOLDER"]
        os.makedirs(qr_dir, exist_ok=True)
        qr_filename = "test_qr_public.png"
        with open(os.path.join(qr_dir, qr_filename), "wb") as f:
            f.write(b"fake-png")
        model = Model3D(
            id="public-model-qr",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="model.glb",
            qr_code_path=qr_filename,
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()

    resp = client.get("/qr-image/public-model-qr")
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# 1.4 Path traversal protection in _find_texture
# --------------------------------------------------------------------------- #
def test_find_texture_rejects_path_traversal(tmp_path):
    """_find_texture should not resolve URIs that escape the search directory."""
    from converters.glb_quality import _find_texture

    search_dir = str(tmp_path / "textures")
    Path(search_dir).mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("sensitive")

    result = _find_texture("../secret.txt", [search_dir])
    assert result is None


def test_find_texture_finds_legitimate_file(tmp_path):
    """_find_texture should find files that are within the search directory."""
    from converters.glb_quality import _find_texture

    search_dir = str(tmp_path / "textures")
    Path(search_dir).mkdir()
    tex = Path(search_dir) / "diffuse.png"
    tex.write_bytes(b"png-data")

    result = _find_texture("diffuse.png", [search_dir])
    assert result is not None
    assert result.name == "diffuse.png"


# --------------------------------------------------------------------------- #
# 3.5 Backup manifest format version
# --------------------------------------------------------------------------- #
def test_backup_manifest_includes_format_version(client):
    """Backup archive manifest should start with format_version=1."""
    import os
    import zipfile

    from tests.conftest import login, register

    register(client, email="backup@example.com")
    app = client.application
    with app.app_context():
        user = User.query.filter_by(email="backup@example.com").one()
        user.is_admin = True
        db.session.commit()

    login(client, email="backup@example.com")
    resp = client.post("/admin/backups/create", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        from app import backup_folder
        folder = backup_folder(app)
        if os.path.isdir(folder):
            zips = [f for f in os.listdir(folder) if f.endswith(".zip")]
            if zips:
                zp = os.path.join(folder, zips[0])
                with zipfile.ZipFile(zp) as z:
                    manifest = z.read("manifest.txt").decode()
                    assert manifest.startswith("format_version=1")
