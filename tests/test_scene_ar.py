"""Scene AR variants: GLB/USDZ with only a saved scene's visible layers, built by the worker."""
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import trimesh
from pygltflib import GLTF2

import app as app_module
from converters.glb_optimize import decompress_glb, glb_has_draco, optimize_glb
from converters.layers import normalize_layers
from converters.scene_variant import build_scene_variant
from institutions import institution_usage
from models import ConversionJob, Institution, Model3D, ModelScene, Paper, User, db
from scenes import scene_ar_keys, scene_to_dict
from tests.conftest import create_user, login, upload_file_bytes
from tests.test_cad_upload_pipeline import FIXTURES, _project, _upload

PASSWORD = "password123"
HIDE_A_FADE_B = {"v": 1, "layers": {"A": {"visible": False, "opacity": 1}, "B": {"visible": True, "opacity": 0.4}}}


def _layered_glb(path, names=("A", "B", "C")):
    scene = trimesh.Scene()
    for i, name in enumerate(names):
        mesh = trimesh.creation.icosphere(subdivisions=3, radius=0.01)
        mesh.apply_translation((i * 0.05, 0, 0))
        mesh.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(name="m", baseColorFactor=[0.5, 0.5, 0.5, 1.0])
        )
        scene.add_geometry(mesh, node_name=name, geom_name=f"g{i}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    scene.export(str(path))
    layers = normalize_layers(str(path))
    optimize_glb(str(path), keep_layers=True)
    return layers


def _plain(path, tmp_path):
    """The GLB without Draco, parsed."""
    plain = tmp_path / f"plain-{uuid.uuid4().hex[:6]}.glb"
    assert decompress_glb(str(path), str(plain))
    return GLTF2.load(str(plain))


def _used_materials(gltf):
    return {gltf.materials[p.material].name for mesh in gltf.meshes for p in mesh.primitives}


def _make_model(app, *, plan="academic", visibility="public", email="owner@example.com", real_glb=True, institution=None):
    with app.app_context():
        user = User.query.filter_by(email=email).first() or create_user(email=email, username="Owner")
        paper = Paper(
            title="Heart", slug=f"p-{uuid.uuid4().hex[:6]}", user_id=user.id,
            is_public=visibility == "public", visibility=visibility,
        )
        db.session.add(paper)
        db.session.flush()
        model_id = str(uuid.uuid4())
        glb = os.path.join(app.config["CONVERTED_FOLDER"], model_id, "model.glb")
        layers = _layered_glb(glb) if real_glb else []
        model = Model3D(
            id=model_id, paper_id=paper.id, user_id=user.id, glb_path=glb, processing_status="ready",
            license_type=plan, source_format="glb", file_size=os.path.getsize(glb) if real_glb else 10,
            original_filename="heart.glb", public_id=uuid.uuid4().hex[:20],
            access_starts_at=datetime.now(UTC), access_expires_at=datetime.now(UTC) + timedelta(days=365),
            layer_info={"layers": layers, "notes": []} if layers else None,
            institution_id=institution,
        )
        db.session.add(model)
        db.session.commit()
        return model_id


def _add_scene(app, model_id, state=None, **fields):
    with app.app_context():
        scene = ModelScene(
            model_id=model_id, public_id=uuid.uuid4().hex[:20], title="Detail", order_index=0,
            state=HIDE_A_FADE_B if state is None else state, **fields,
        )
        db.session.add(scene)
        db.session.commit()
        return scene.id


@pytest.fixture()
def usdz(monkeypatch):
    """Stand-in for Blender: records the GLB it was given and writes a small USDZ."""
    calls = []

    def fake(glb_path, usdz_path):
        calls.append(GLTF2.load(glb_path) if not glb_has_draco(glb_path) else "draco")
        Path(usdz_path).write_bytes(b"PK-usdz" * 10)
        return True

    monkeypatch.setattr(app_module, "convert_glb_to_usdz", fake)
    return calls


@pytest.fixture()
def r2(monkeypatch):
    synced, deleted = [], []
    monkeypatch.setattr(app_module, "mirror_file_sync", lambda path, key: synced.append(key) or True)
    monkeypatch.setattr(app_module, "mirror_delete", lambda key: deleted.append(key))
    monkeypatch.setattr("services.r2_mirror.mirror_delete", lambda key: deleted.append(key))
    return synced, deleted


# --- the variant file -----------------------------------------------------------------


def test_variant_keeps_only_visible_layers_and_fades_the_rest(tmp_path):
    source = tmp_path / "src.glb"
    _layered_glb(source)
    out = tmp_path / "out" / "variant.glb"

    assert build_scene_variant(str(source), str(out), {"A"}, {"B": 0.4}) is True

    assert glb_has_draco(str(out))
    gltf = _plain(out, tmp_path)
    assert _used_materials(gltf) == {"B", "C"}
    assert len(gltf.meshes) == 2 and all(m.primitives for m in gltf.meshes)
    assert all(node.mesh is None or node.mesh < len(gltf.meshes) for node in gltf.nodes)
    materials = {m.name: m for m in gltf.materials}
    assert "A" not in materials  # pruned together with its geometry
    assert materials["B"].alphaMode == "BLEND"
    assert materials["B"].pbrMetallicRoughness.baseColorFactor[3] == pytest.approx(0.4)
    assert materials["C"].alphaMode != "BLEND"
    assert len(trimesh.load(str(next(tmp_path.glob("plain-*.glb"))), force="scene").geometry) == 2
    assert out.stat().st_size < source.stat().st_size


def test_uncompressed_variant_keeps_faded_layers(tmp_path):
    source = tmp_path / "src.glb"
    _layered_glb(source)
    out = tmp_path / "plain_variant.glb"

    assert build_scene_variant(str(source), str(out), {"A"}, {"B": 0.4, "C": 0.8}, compress=False)

    assert not glb_has_draco(str(out))
    gltf = GLTF2.load(str(out))
    assert _used_materials(gltf) == {"B", "C"}
    assert {m.name for m in gltf.materials if m.alphaMode == "BLEND"} == {"B", "C"}


def test_variant_fails_quietly_when_nothing_is_left_or_the_source_is_bad(tmp_path):
    source = tmp_path / "src.glb"
    _layered_glb(source)
    out = tmp_path / "out.glb"
    assert build_scene_variant(str(source), str(out), {"A", "B", "C"}, {}) is False
    assert not out.exists()
    assert build_scene_variant(str(tmp_path / "missing.glb"), str(out), {"A"}, {}) is False
    (tmp_path / "junk.glb").write_bytes(b"not a glb")
    assert build_scene_variant(str(tmp_path / "junk.glb"), str(out), {"A"}, {}) is False
    assert not out.exists()


# --- the job -----------------------------------------------------------------------------


def test_job_builds_glb_and_usdz_and_mirrors_both(app, usdz, r2):
    synced, _ = r2
    model_id = _add_and_run(app)
    with app.app_context():
        scene = db.session.get(ModelScene, 1)
        assert scene.ar_status == "ready"
        glb = os.path.join(app.config["CONVERTED_FOLDER"], model_id, "scenes", "1.glb")
        usdz_path = glb[:-4] + ".usdz"
        assert scene.ar_glb_path == glb and scene.ar_usdz_path == usdz_path
        assert scene.ar_file_size == os.path.getsize(glb) + os.path.getsize(usdz_path)
        assert db.session.get(Model3D, model_id).processing_status == "ready"
        job = ConversionJob.query.filter_by(job_type="scene_ar").one()
        assert job.status == "completed" and job.model_id == model_id and job.payload == {"scene_id": 1}
    assert synced == scene_ar_keys(model_id, 1)
    # The USDZ is exported from the scene's own GLB (faded B keeps its opacity).
    assert len(usdz) == 1


def _add_and_run(app, **kwargs):
    model_id = _make_model(app, **kwargs)
    scene_id = _add_scene(app, model_id, ar_status="queued")
    assert scene_id == 1
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        app_module.enqueue_conversion_job(app, model=model, job_kwargs={"scene_id": scene_id}, job_type="scene_ar")
    return model_id


def test_scene_that_hides_nothing_gets_no_variant_and_loses_an_old_one(app, usdz, r2):
    _, deleted = r2
    model_id = _make_model(app)
    folder = Path(app.config["CONVERTED_FOLDER"]) / model_id / "scenes"
    folder.mkdir()
    (folder / "1.glb").write_bytes(b"old")
    (folder / "1.usdz").write_bytes(b"old")
    state = {"v": 1, "layers": {"A": {"visible": True, "opacity": 1}, "Gone": {"visible": False, "opacity": 1}}}
    _add_scene(app, model_id, state, ar_status="queued", ar_glb_path=str(folder / "1.glb"), ar_file_size=6)
    with app.app_context():
        app_module.process_scene_ar_job(app, scene_id=1)
        scene = db.session.get(ModelScene, 1)
        assert (scene.ar_status, scene.ar_glb_path, scene.ar_usdz_path, scene.ar_file_size) == ("none", None, None, None)
    assert not list(folder.iterdir())
    assert deleted == scene_ar_keys(model_id, 1)  # stale layer names are ignored: nothing to build


def test_failed_build_marks_the_scene_failed_but_not_the_model(app, usdz, r2, monkeypatch):
    model_id = _make_model(app)
    _add_scene(app, model_id, ar_status="queued")
    monkeypatch.setattr(app_module, "build_scene_variant", lambda *a, **k: False)
    with app.app_context():
        app_module.process_scene_ar_job(app, scene_id=1)
        assert db.session.get(ModelScene, 1).ar_status == "failed"
        model = db.session.get(Model3D, model_id)
        assert model.processing_status == "ready" and os.path.exists(model.glb_path)
    assert not (Path(app.config["CONVERTED_FOLDER"]) / model_id / "scenes" / "1.glb").exists()


def test_crash_in_the_build_is_contained(app, usdz, r2, monkeypatch):
    model_id = _make_model(app)
    _add_scene(app, model_id, ar_status="queued")

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_module, "build_scene_variant", boom)
    with app.app_context():
        app_module.process_scene_ar_job(app, scene_id=1)
        assert db.session.get(ModelScene, 1).ar_status == "failed"
        assert db.session.get(Model3D, model_id).processing_status == "ready"


def test_low_disk_space_fails_the_scene_before_building(app, usdz, r2, monkeypatch):
    from collections import namedtuple

    model_id = _make_model(app)
    _add_scene(app, model_id, ar_status="queued")
    app.config["STORAGE_MIN_FREE_BYTES"] = 10**9
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr("shutil.disk_usage", lambda path: Usage(10**12, 10**12 - 1000, 1000))
    built = []
    monkeypatch.setattr(app_module, "build_scene_variant", lambda *a, **k: built.append(1) or True)
    with app.app_context():
        app_module.process_scene_ar_job(app, scene_id=1)
        assert db.session.get(ModelScene, 1).ar_status == "failed"
        assert db.session.get(Model3D, model_id).processing_status == "ready"
    assert not built


def test_worker_dispatches_scene_ar_and_gives_up_without_failing_the_model(app, usdz, r2):
    model_id = _make_model(app)
    _add_scene(app, model_id, ar_status="queued")
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        job = ConversionJob(job_type="scene_ar", status="pending", model_id=model_id, user_id=model.user_id, payload={"scene_id": 1})
        db.session.add(job)
        db.session.commit()
        assert app_module.run_next_conversion_job(app) is True
        assert db.session.get(ModelScene, 1).ar_status == "ready"
        assert db.session.get(ConversionJob, job.id).status == "completed"

        # An attempt-exhausted or reclaimed scene_ar job is failed on its own.
        scene = db.session.get(ModelScene, 1)
        scene.ar_status = "queued"
        stuck = ConversionJob(
            job_type="scene_ar", status="pending", model_id=model_id, user_id=model.user_id,
            payload={"scene_id": 1}, attempts=3, max_attempts=3,
        )
        db.session.add(stuck)
        db.session.commit()
        assert app_module.run_next_conversion_job(app) is True
        assert db.session.get(ConversionJob, stuck.id).status == "failed"
        assert db.session.get(ModelScene, 1).ar_status == "failed"
        assert db.session.get(Model3D, model_id).processing_status == "ready"

        reaped = ConversionJob(
            job_type="scene_ar", status="processing", model_id=model_id, user_id=model.user_id,
            payload={"scene_id": 1}, attempts=3, max_attempts=3, started_at=datetime.now(UTC) - timedelta(days=1),
        )
        db.session.add(reaped)
        db.session.commit()
        assert app_module.reclaim_stuck_conversion_jobs(app) == 1
        assert db.session.get(ConversionJob, reaped.id).status == "failed"
        assert db.session.get(Model3D, model_id).processing_status == "ready"


# --- enqueueing from the scene routes ------------------------------------------------------


@pytest.fixture()
def queued(monkeypatch):
    jobs = []
    monkeypatch.setattr(app_module, "enqueue_conversion_job", lambda app, **kw: jobs.append(kw["job_kwargs"]))
    return jobs


def _post(client, url, **payload):
    return client.post(url, json=payload)


def test_create_and_update_enqueue_only_when_hidden_or_faded_layers_change(app, client, queued):
    model_id = _make_model(app)
    login(client, email="owner@example.com", password=PASSWORD)
    shown = {"v": 1, "layers": {"A": {"visible": True, "opacity": 1}}}

    plain = _post(client, f"/models/{model_id}/scenes", title="All", state=shown).get_json()["scene"]
    assert queued == [] and plain["ar_status"] == "none"

    created = _post(client, f"/models/{model_id}/scenes", title="Cut", state=HIDE_A_FADE_B).get_json()["scene"]
    assert queued == [{"scene_id": created["id"]}] and created["ar_status"] == "queued"
    assert created["ar_glb_url"] is None and created["ar_usdz_url"] is None  # only once ready

    url = f"/models/{model_id}/scenes/{created['id']}"
    _post(client, url, title="Renamed")
    _post(client, url, state={**HIDE_A_FADE_B, "labels": True, "background": "white"})  # same layers
    assert len(queued) == 1

    _post(client, url, state={"v": 1, "layers": {"A": {"visible": False, "opacity": 1}, "B": {"visible": False, "opacity": 1}}})
    assert len(queued) == 2

    resaved = _post(client, url, state=shown).get_json()["scene"]  # nothing hidden any more
    assert len(queued) == 2 and resaved["ar_status"] == "none"


def test_plans_without_scene_ar_never_enqueue(app, client, queued, monkeypatch):
    import scenes

    real = scenes.plan_supports_feature
    monkeypatch.setattr(scenes, "plan_supports_feature", lambda plan, feature: False if feature == "scene_ar" else real(plan, feature))
    model_id = _make_model(app)
    login(client, email="owner@example.com", password=PASSWORD)
    scene = _post(client, f"/models/{model_id}/scenes", title="Cut", state=HIDE_A_FADE_B).get_json()["scene"]
    _post(client, f"/models/{model_id}/scenes/{scene['id']}", state={"v": 1, "layers": {"B": {"visible": False, "opacity": 1}}})
    assert queued == [] and scene["ar_status"] == "none"


def test_saving_a_scene_builds_its_variant_end_to_end(app, client, usdz, r2):
    model_id = _make_model(app)
    login(client, email="owner@example.com", password=PASSWORD)
    scene = _post(client, f"/models/{model_id}/scenes", title="Cut", state=HIDE_A_FADE_B).get_json()["scene"]
    with app.app_context():
        assert db.session.get(ModelScene, scene["id"]).ar_status == "ready"  # TESTING runs the job inline
    page = client.get(f"/view/{model_id}").get_data(as_text=True)
    assert f"/files/{model_id}/scenes/{scene['id']}.glb" in page and f"/files/{model_id}/scenes/{scene['id']}.usdz" in page


def test_deleting_a_scene_removes_its_files_and_r2_objects(app, client, usdz, r2):
    _, deleted = r2
    model_id = _make_model(app)
    login(client, email="owner@example.com", password=PASSWORD)
    scene = _post(client, f"/models/{model_id}/scenes", title="Cut", state=HIDE_A_FADE_B).get_json()["scene"]
    folder = Path(app.config["CONVERTED_FOLDER"]) / model_id / "scenes"
    assert (folder / f"{scene['id']}.glb").exists() and (folder / f"{scene['id']}.usdz").exists()
    deleted.clear()

    assert client.post(f"/models/{model_id}/scenes/{scene['id']}/delete").status_code == 200

    assert not list(folder.iterdir())
    assert sorted(deleted) == sorted(scene_ar_keys(model_id, scene["id"]))


def test_deleting_a_model_deletes_every_scene_object_from_r2(app, client, usdz, r2):
    _, deleted = r2
    model_id = _make_model(app)
    login(client, email="owner@example.com", password=PASSWORD)
    first = _post(client, f"/models/{model_id}/scenes", title="One", state=HIDE_A_FADE_B).get_json()["scene"]
    second = _post(client, f"/models/{model_id}/scenes", title="Two", state={"v": 1, "layers": {"C": {"visible": False, "opacity": 1}}}).get_json()["scene"]
    deleted.clear()

    client.post(f"/models/{model_id}/delete")

    assert set(scene_ar_keys(model_id, first["id"]) + scene_ar_keys(model_id, second["id"])) <= set(deleted)
    assert f"converted/{model_id}/model.glb" in deleted
    assert not (Path(app.config["CONVERTED_FOLDER"]) / model_id).exists()  # local scenes/ goes with the model folder


def test_replacing_the_model_rebuilds_scene_variants(client, usdz, r2):
    slug = _project(client)
    _upload(client, slug, "gearbox.step", (FIXTURES / "assembly_colored.step").read_bytes())
    with client.application.app_context():
        model = Model3D.query.one()
        model.license_type = "academic"
        db.session.commit()
        model_id = model.id
    state = {"v": 1, "layers": {"Housing": {"visible": False, "opacity": 1}}}
    created = client.post(f"/models/{model_id}/scenes", json={"title": "No housing", "state": state})
    assert created.status_code == 201
    with client.application.app_context():
        scene = ModelScene.query.one()
        assert scene.ar_status == "ready"
        variant = _plain(scene.ar_glb_path, Path(client.application.config["CONVERTED_FOLDER"]))
        assert _used_materials(variant) == {"Shaft", "Cap"}
        scene.ar_status = "failed"  # prove the replacement queues it again
        db.session.commit()
        jobs_before = ConversionJob.query.filter_by(job_type="scene_ar").count()

    client.post(
        f"/models/{model_id}/replace",
        data={"file": upload_file_bytes((FIXTURES / "assembly_colored.step").read_bytes(), "again.step"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with client.application.app_context():
        assert ConversionJob.query.filter_by(job_type="scene_ar").count() == jobs_before + 1
        assert db.session.get(ModelScene, 1).ar_status == "ready"


# --- quota -----------------------------------------------------------------------------------


def test_scene_variants_count_toward_the_institution_storage_quota(app):
    with app.app_context():
        institution = Institution(name="Lab", slug="lab", contract_ends_at=datetime.now(UTC) + timedelta(days=100), quota_storage_bytes=10**9)
        db.session.add(institution)
        db.session.commit()
        institution_id = institution.id
    funded = _make_model(app, plan="institutional", institution=institution_id, real_glb=False)
    other = _make_model(app, plan="academic", real_glb=False)
    _add_scene(app, funded, ar_status="ready", ar_file_size=700)
    _add_scene(app, funded, ar_status="ready", ar_file_size=300)
    _add_scene(app, other, ar_status="ready", ar_file_size=5000)  # not institution funded
    with app.app_context():
        assert institution_usage(institution_id) == (1, 10 + 1000)


# --- serving -----------------------------------------------------------------------------------


def _ready_scene(app, usdz, r2, **model_kwargs):
    model_id = _make_model(app, **model_kwargs)
    _add_scene(app, model_id, ar_status="queued")
    with app.app_context():
        app_module.process_scene_ar_job(app, scene_id=1)
        assert db.session.get(ModelScene, 1).ar_status == "ready"
    return model_id


def test_scene_files_are_served_with_the_models_access_rules(app, client, usdz, r2):
    model_id = _ready_scene(app, usdz, r2)
    glb = client.get(f"/files/{model_id}/scenes/1.glb")
    assert glb.status_code == 200 and glb.mimetype == "model/gltf-binary"
    assert glb.headers["Cache-Control"] == "private, no-cache"
    glb.close()
    usd = client.get(f"/files/{model_id}/scenes/1.usdz")
    assert usd.status_code == 200 and usd.mimetype == "model/vnd.usdz+zip"
    usd.close()
    assert client.get(f"/files/{model_id}/scenes/2.glb").status_code == 404
    assert client.get(f"/files/{model_id}/scenes/1.txt").status_code == 404
    assert client.get(f"/files/not-a-uuid/scenes/1.glb").status_code == 404
    with app.test_request_context():
        scene = db.session.get(ModelScene, 1)
        data = scene_to_dict(scene)
        assert data["ar_status"] == "ready"
        assert data["ar_glb_url"] == f"/files/{model_id}/scenes/1.glb" and data["ar_usdz_url"].endswith("/scenes/1.usdz")


def test_private_project_expired_model_and_missing_plan_feature_block_scene_files(app, client, usdz, r2, monkeypatch):
    private = _ready_scene(app, usdz, r2, visibility="private")
    assert client.get(f"/files/{private}/scenes/1.glb").status_code == 404
    login(client, email="owner@example.com", password=PASSWORD)
    assert client.get(f"/files/{private}/scenes/1.glb").status_code == 200  # the owner still sees it
    assert client.get(f"/files/{private}/scenes/1.usdz").status_code == 200
    client.get("/auth/logout")

    with app.app_context():
        db.session.get(Model3D, private).paper.visibility = "public"
        db.session.get(Model3D, private).paper.is_public = True
        db.session.commit()
        assert client.get(f"/files/{private}/scenes/1.glb").status_code == 200
        db.session.get(Model3D, private).access_expires_at = datetime.now(UTC) - timedelta(days=1)
        db.session.commit()
    assert client.get(f"/files/{private}/scenes/1.glb").status_code == 404

    with app.app_context():
        db.session.get(Model3D, private).access_expires_at = datetime.now(UTC) + timedelta(days=5)
        db.session.commit()
    import scenes

    real = scenes.plan_supports_feature
    monkeypatch.setattr(scenes, "plan_supports_feature", lambda plan, feature: False if feature == "scene_ar" else real(plan, feature))
    assert client.get(f"/files/{private}/scenes/1.glb").status_code == 404  # downgraded: no scene AR
    with app.test_request_context():
        assert scene_to_dict(db.session.get(ModelScene, 1))["ar_glb_url"] is None


def test_scene_files_wait_until_the_variant_is_ready_and_come_back_from_r2(app, client, usdz, r2, monkeypatch):
    model_id = _ready_scene(app, usdz, r2)
    local = Path(app.config["CONVERTED_FOLDER"]) / model_id / "scenes" / "1.glb"
    restored = []

    def restore(path, key):
        restored.append(key)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"glTF-restored")
        return True

    local.unlink()
    monkeypatch.setattr("services.r2_mirror.ensure_local", restore)
    response = client.get(f"/files/{model_id}/scenes/1.glb")
    assert response.status_code == 200 and response.data == b"glTF-restored"
    assert restored == [f"converted/{model_id}/scenes/1.glb"]

    with app.app_context():
        db.session.get(ModelScene, 1).ar_status = "queued"
        db.session.commit()
    assert client.get(f"/files/{model_id}/scenes/1.glb").status_code == 404


def _layer_form(app, model_id, rename=None, recolour=None):
    with app.app_context():
        layers = db.session.get(Model3D, model_id).layer_info["layers"]
    data = {}
    for i, layer in enumerate(layers):
        data[f"layer_name_{i}"] = (rename or {}).get(layer["name"], layer["name"])
        if layer.get("color"):
            data[f"layer_color_{i}"] = (recolour or {}).get(layer["name"], layer["color"])
    return data


def test_renaming_layers_keeps_saved_scenes_in_step(app, client, queued):
    model_id = _make_model(app)
    scene_id = _add_scene(app, model_id)
    login(client, email="owner@example.com", password=PASSWORD)
    client.post(f"/models/{model_id}/layers", data=_layer_form(app, model_id, rename={"A": "B", "B": "A"}))
    with app.app_context():
        layers = db.session.get(ModelScene, scene_id).state["layers"]
    assert layers == {"B": {"visible": False, "opacity": 1}, "A": {"visible": True, "opacity": 0.4}}  # swap is safe


def test_recolouring_layers_rebuilds_scene_variants(app, client, queued):
    model_id = _make_model(app)
    scene_id = _add_scene(app, model_id)
    login(client, email="owner@example.com", password=PASSWORD)
    client.post(f"/models/{model_id}/layers", data=_layer_form(app, model_id, recolour={"C": "#ff0000"}))
    assert {"scene_id": scene_id} in queued
    with app.app_context():
        assert db.session.get(ModelScene, scene_id).ar_status == "queued"
