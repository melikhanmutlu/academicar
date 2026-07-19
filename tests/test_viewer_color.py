"""Tests for the viewer's owner-only 'apply color to model' flow and the
lightweight USDZ-regeneration worker job it enqueues."""
import uuid
from datetime import UTC, datetime, timedelta

from models import ConversionJob, Model3D, Paper, User, db


def _owned_ready_model(app, email="vc@example.com"):
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(email=email, username="VC")
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
        paper = Paper(title="P", slug=f"vc-{uuid.uuid4().hex[:6]}", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        mid = str(uuid.uuid4())
        model = Model3D(
            id=mid, paper_id=paper.id, user_id=user.id,
            glb_path=f"converted/{mid}/model.glb", processing_status="ready",
            license_type="academic", source_format="stl", appearance_color="#cccccc",
            access_starts_at=datetime.now(UTC),
            access_expires_at=datetime.now(UTC) + timedelta(days=365),
        )
        db.session.add(model)
        db.session.commit()
        return mid


def test_viewer_color_rejects_bad_hex(client, app):
    from tests.conftest import login

    mid = _owned_ready_model(app)
    login(client, email="vc@example.com", password="password123")
    resp = client.post(f"/models/{mid}/viewer-color", json={"color": "notacolor"})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_viewer_color_requires_ownership(client, app):
    from tests.conftest import create_user, login

    mid = _owned_ready_model(app)
    with app.app_context():
        create_user(email="intruder-vc@example.com", username="Intruder")
    login(client, email="intruder-vc@example.com", password="password123")
    resp = client.post(f"/models/{mid}/viewer-color", json={"color": "#336699"})
    assert resp.status_code == 403


def test_viewer_color_bakes_and_enqueues_usdz(client, app, monkeypatch):
    """A valid save recolors via the appearance pipeline and enqueues a
    usdz_regen job (the actual GLB bake is covered by the appearance tests)."""
    from tests.conftest import login

    mid = _owned_ready_model(app, email="vc2@example.com")
    login(client, email="vc2@example.com", password="password123")

    # Stub the GLB recolor (needs a real GLB on disk otherwise) so the test can
    # focus on the endpoint's own logic: validation, audit, and USDZ enqueue.
    monkeypatch.setattr(
        "app._apply_model_appearance_change",
        lambda model, form: (True, "ok", "success", {"color": form.get("color")}),
    )
    resp = client.post(f"/models/{mid}/viewer-color", json={"color": "#3366CC"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    with app.app_context():
        jobs = ConversionJob.query.filter_by(model_id=mid, job_type="usdz_regen").all()
        assert len(jobs) == 1


def test_usdz_regen_job_regenerates_and_completes(app, tmp_path, monkeypatch):
    """process_usdz_regen_job re-exports the USDZ from the current GLB and marks
    the job completed, without touching the GLB."""
    import app as app_module

    mid = _owned_ready_model(app, email="vc3@example.com")
    glb = tmp_path / "model.glb"
    glb.write_bytes(b"glTF-fake-but-present")
    usdz = tmp_path / "model.usdz"

    calls = {}

    def fake_convert(glb_path, usdz_path):
        calls["glb"] = glb_path
        # Simulate a produced companion so the mirror branch runs.
        with open(usdz_path, "wb") as fh:
            fh.write(b"usdz")
        return True

    monkeypatch.setattr(app_module, "convert_glb_to_usdz", fake_convert)
    monkeypatch.setattr(app_module, "mirror_file", lambda *a, **k: True)

    with app.app_context():
        job = ConversionJob(job_type="usdz_regen", status="pending", model_id=mid)
        db.session.add(job)
        db.session.commit()
        job_id = job.id

    app_module.process_usdz_regen_job(
        app, model_id=mid, glb_path=str(glb), usdz_path=str(usdz), job_id=job_id
    )

    assert calls.get("glb") == str(glb)
    assert glb.read_bytes() == b"glTF-fake-but-present"  # GLB untouched
    with app.app_context():
        assert db.session.get(ConversionJob, job_id).status == "completed"
