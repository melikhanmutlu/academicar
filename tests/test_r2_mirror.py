"""Tests for the Cloudflare R2 durable mirror: restore-on-miss logic and the
serve route restoring a file from R2 after the local (ephemeral) copy is gone."""
import os
import uuid

from services import r2_mirror


class _FakeR2Client:
    """Minimal stand-in for the boto3 S3 client used by r2_mirror."""

    def __init__(self, store: dict[str, bytes]):
        self.store = store

    def download_file(self, bucket, key, dest):
        if key not in self.store:
            raise FileNotFoundError(key)
        with open(dest, "wb") as fh:
            fh.write(self.store[key])


def test_ensure_local_true_when_file_present(tmp_path):
    p = tmp_path / "present.bin"
    p.write_bytes(b"hi")
    assert r2_mirror.ensure_local(str(p), "converted/x/model.glb") is True


def test_ensure_local_noop_when_r2_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(r2_mirror, "_get_client", lambda: None)
    p = tmp_path / "missing.bin"
    assert r2_mirror.ensure_local(str(p), "converted/x/model.glb") is False
    assert not p.exists()


def test_ensure_local_restores_from_r2(tmp_path, monkeypatch):
    store = {"converted/abc/model.glb": b"GLBDATA"}
    monkeypatch.setattr(r2_mirror, "_get_client", lambda: _FakeR2Client(store))
    monkeypatch.setattr(r2_mirror, "_bucket", lambda: "test-bucket")
    dest = tmp_path / "nested" / "model.glb"  # parent dir intentionally absent
    assert not dest.exists()
    assert r2_mirror.ensure_local(str(dest), "converted/abc/model.glb") is True
    assert dest.read_bytes() == b"GLBDATA"


def test_restore_file_missing_object_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(r2_mirror, "_get_client", lambda: _FakeR2Client({}))
    monkeypatch.setattr(r2_mirror, "_bucket", lambda: "b")
    dest = tmp_path / "x.glb"
    assert r2_mirror.restore_file(str(dest), "converted/none/model.glb") is False
    assert not dest.exists()
    assert not (tmp_path / "x.glb.r2restore").exists()  # no leftover temp


def test_serve_glb_restores_from_r2_after_volume_wipe(client, app, monkeypatch):
    """End-to-end: a public model whose local GLB is gone is restored from the
    R2 mirror on request, so Railway keeps serving after a volume recycle."""
    from licensing import apply_model_license_defaults
    from models import Model3D, Paper, User, db

    model_id = str(uuid.uuid4())
    with app.app_context():
        user = User(email="r2serve@example.com", username="R2")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(title="T", slug=f"r2-{uuid.uuid4().hex[:6]}", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        glb_local = os.path.join(app.config["CONVERTED_FOLDER"], model_id, "model.glb")
        model = Model3D(
            id=model_id,
            paper_id=paper.id,
            user_id=user.id,
            glb_path=glb_local,
            processing_status="ready",
        )
        apply_model_license_defaults(model, "academic")  # active, far-future expiry
        db.session.add(model)
        db.session.commit()

    store = {f"converted/{model_id}/model.glb": b"glTF-bytes-from-r2"}
    monkeypatch.setattr(r2_mirror, "_get_client", lambda: _FakeR2Client(store))
    monkeypatch.setattr(r2_mirror, "_bucket", lambda: "test-bucket")

    # Local copy is absent (as after a volume wipe) but present in the mirror.
    assert not os.path.exists(glb_local)
    resp = client.get(f"/files/{model_id}/model.glb")
    assert resp.status_code == 200
    assert resp.data == b"glTF-bytes-from-r2"
    assert os.path.exists(glb_local)  # restored to the local volume


def test_serve_glb_404_when_missing_and_no_r2(client, app, monkeypatch):
    """With R2 disabled and no local file, the route still 404s cleanly."""
    from licensing import apply_model_license_defaults
    from models import Model3D, Paper, User, db

    monkeypatch.setattr(r2_mirror, "_get_client", lambda: None)
    model_id = str(uuid.uuid4())
    with app.app_context():
        user = User(email="r2none@example.com", username="R2")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(title="T", slug=f"r2n-{uuid.uuid4().hex[:6]}", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        glb_local = os.path.join(app.config["CONVERTED_FOLDER"], model_id, "model.glb")
        model = Model3D(id=model_id, paper_id=paper.id, user_id=user.id, glb_path=glb_local, processing_status="ready")
        apply_model_license_defaults(model, "academic")
        db.session.add(model)
        db.session.commit()

    assert client.get(f"/files/{model_id}/model.glb").status_code == 404
