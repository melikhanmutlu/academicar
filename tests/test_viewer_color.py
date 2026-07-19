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
    """A valid save bakes the color into the GLB and enqueues a usdz_regen job
    (the actual baseColorFactor write is covered by the set_base_color_factor
    tests below)."""
    from tests.conftest import login

    mid = _owned_ready_model(app, email="vc2@example.com")
    login(client, email="vc2@example.com", password="password123")

    # Stub the GLB bake (needs a real GLB on disk otherwise) so the test can
    # focus on the endpoint's own logic: validation, audit, and USDZ enqueue.
    monkeypatch.setattr("app._bake_viewer_color", lambda model, color: (True, "ok"))
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


# --- set_base_color_factor: the actual GLB bake behind the viewer flow --------
# Regression for "Apply to model does nothing on textured models": the previous
# path only tuned metallic/roughness on textured GLBs, so the chosen colour was
# discarded. set_base_color_factor must set baseColorFactor on every material
# (textured or not) while keeping textures and per-material alpha intact.


def _textured_glb(tmp_path):
    """A one-material GLB whose material carries a baseColorTexture."""
    import io

    import numpy as np
    import trimesh
    from PIL import Image as PILImage
    from pygltflib import (
        GLTF2,
        BufferView,
        Image,
        PbrMetallicRoughness,
        Texture,
        TextureInfo,
    )

    from converters.glb_quality import ensure_pbr_materials

    glb_path = str(tmp_path / "tex.glb")
    trimesh.Scene([trimesh.creation.box(extents=[1, 1, 1])]).export(glb_path)
    ensure_pbr_materials(glb_path)
    g = GLTF2.load(glb_path)

    im = PILImage.fromarray(np.full((4, 4, 4), [128, 128, 128, 255], dtype=np.uint8), "RGBA")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    png = buf.getvalue()

    blob = g.binary_blob() or b""
    pad = b"\x00" * ((4 - len(blob) % 4) % 4)
    offset = len(blob) + len(pad)
    g.set_binary_blob(blob + pad + png)
    g.buffers[0].byteLength = len(g.binary_blob())
    g.bufferViews.append(BufferView(buffer=0, byteOffset=offset, byteLength=len(png)))
    if g.images is None:
        g.images = []
    g.images.append(Image(bufferView=len(g.bufferViews) - 1, mimeType="image/png"))
    if g.textures is None:
        g.textures = []
    g.textures.append(Texture(source=len(g.images) - 1))
    g.materials[0].pbrMetallicRoughness = PbrMetallicRoughness(
        baseColorFactor=[1.0, 1.0, 1.0, 1.0],
        baseColorTexture=TextureInfo(index=len(g.textures) - 1),
    )
    g.save(glb_path)
    return glb_path


def test_set_base_color_factor_colors_textured_material_and_keeps_texture(tmp_path):
    from pygltflib import GLTF2

    from converters.glb_quality import set_base_color_factor

    glb = _textured_glb(tmp_path)
    assert set_base_color_factor(glb, (0.2, 0.4, 0.8)) is True

    g = GLTF2.load(glb)
    pbr = g.materials[0].pbrMetallicRoughness
    # The chosen colour is now baked (this is exactly what did NOT happen before).
    assert pbr.baseColorFactor[:3] == [0.2, 0.4, 0.8]
    assert pbr.baseColorFactor[3] == 1.0
    # ...and the texture is preserved (a tint, not a flat-colour replacement).
    assert pbr.baseColorTexture is not None


def test_set_base_color_factor_preserves_existing_alpha(tmp_path):
    from pygltflib import GLTF2

    from converters.glb_quality import ensure_pbr_materials, set_base_color_factor

    import trimesh

    glb = str(tmp_path / "plain.glb")
    trimesh.Scene([trimesh.creation.box(extents=[1, 1, 1])]).export(glb)
    ensure_pbr_materials(glb)
    g = GLTF2.load(glb)
    g.materials[0].pbrMetallicRoughness.baseColorFactor = [0.9, 0.1, 0.1, 0.5]
    g.save(glb)

    assert set_base_color_factor(glb, (0.2, 0.4, 0.8)) is True
    g2 = GLTF2.load(glb)
    bcf = g2.materials[0].pbrMetallicRoughness.baseColorFactor
    assert bcf[:3] == [0.2, 0.4, 0.8]
    assert bcf[3] == 0.5  # translucent material keeps its opacity
