"""Storage admin page: deleted projects are excluded, one converted walk,
conditional orphan row, backfill skips deleted projects."""
import os
from datetime import UTC, datetime

import app as app_module
from models import ConversionJob, Model3D, db
from tests.test_admin_enrichment import _seed_model
from tests.test_admin_panel import _make_admin
from tests.test_layer_metrics import _make_model


def _delete_project(app, model_id):
    with app.app_context():
        db.session.get(Model3D, model_id).paper.status = "deleted"
        db.session.commit()


def test_scan_converted_folder_single_walk_counts_size_files_orphans(tmp_path):
    (tmp_path / "m1" / "scenes").mkdir(parents=True)
    (tmp_path / "m1" / "model.glb").write_bytes(b"x" * 10)
    (tmp_path / "m1" / "scenes" / "s.glb").write_bytes(b"x" * 5)
    (tmp_path / "gone").mkdir()
    (tmp_path / "gone" / "model.glb").write_bytes(b"x" * 7)
    (tmp_path / "stray.bin").write_bytes(b"x" * 3)
    assert app_module.scan_converted_folder(str(tmp_path), {"m1"}) == (25, 4, 2)
    assert app_module.scan_converted_folder(str(tmp_path / "nope"), set()) == (0, 0, 0)


def test_storage_page_excludes_deleted_projects(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        model.r2_mirror_failed_at = datetime.now(UTC)
        model.file_size = 1234567
        db.session.commit()
    html = client.get("/admin/storage").get_data(as_text=True)
    assert "R2 mirror failures" in html and "1.2 MB" in html
    _delete_project(client.application, model_id)
    html = client.get("/admin/storage").get_data(as_text=True)
    assert "R2 mirror failures" not in html
    assert "1.2 MB" not in html
    assert "No models yet." in html


def test_orphan_row_only_when_orphans_exist(client):
    _make_admin(client)
    assert "Orphan files" not in client.get("/admin/storage").get_data(as_text=True)
    converted = client.application.config["CONVERTED_FOLDER"]
    os.makedirs(os.path.join(converted, "gone"), exist_ok=True)
    with open(os.path.join(converted, "gone", "model.glb"), "wb") as fh:
        fh.write(b"x")
    assert "Orphan files" in client.get("/admin/storage").get_data(as_text=True)


def test_layer_metrics_backfill_skips_deleted_projects(client, app):
    model_id = _make_model(app)
    _delete_project(app, model_id)
    _make_admin(client)
    html = client.post("/admin/layer-metrics/backfill", follow_redirects=True).get_data(as_text=True)
    assert "No models need layer measurements." in html
    with app.app_context():
        assert ConversionJob.query.filter_by(job_type="layer_metrics").count() == 0
