"""Per-layer measurements: known geometry, pipeline storage, backfill job, rename, admin trigger."""
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import trimesh

from converters import layer_metrics
from converters.glb_optimize import glb_has_draco, optimize_glb
from converters.layer_metrics import compute_layer_metrics
from converters.layers import normalize_layers, read_layers
from models import ConversionJob, Model3D, Paper, User, db
from tests.conftest import create_user, login
from tests.test_admin_panel import _make_admin
from tests.test_medical_upload_pipeline import _labelmap, _project, _upload

FIXTURES = Path(__file__).parent / "fixtures" / "step"
MM = 0.001


def _layered_glb(path, parts):
    """Write a GLB with one named part (own material) per ``(name, mesh)``."""
    scene = trimesh.Scene()
    for i, (name, mesh) in enumerate(parts):
        mesh.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(name="m", baseColorFactor=[0.5, 0.5, 0.5, 1.0])
        )
        scene.add_geometry(mesh, node_name=name, geom_name=f"g{i}")
    scene.export(str(path))
    return normalize_layers(str(path))


def _sphere(x_mm=0.0, r_mm=10.0, subdivisions=4):
    mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=r_mm * MM)
    mesh.apply_translation((x_mm * MM, 0, 0))
    return mesh


def _two_spheres_and_box(path):
    box = trimesh.creation.box(extents=(40 * MM, 20 * MM, 10 * MM))
    box.apply_translation((0.3, 0, 0))
    return _layered_glb(path, [("Sphere A", _sphere(0)), ("Sphere B", _sphere(40)), ("Box", box)])


def test_known_geometry_dimensions_and_pair_distance(tmp_path):
    glb = tmp_path / "m.glb"
    layers = _two_spheres_and_box(glb)

    metrics = compute_layer_metrics(str(glb), layers)

    assert metrics["version"] == 1 and metrics["unit"] == "mm" and "truncated" not in metrics
    assert set(metrics["layers"]) == {"Sphere A", "Sphere B", "Box"}
    box = metrics["layers"]["Box"]
    assert box["dims_mm"] == pytest.approx([40, 20, 10], rel=0.02)
    assert box["max_diameter_mm"] == pytest.approx((40**2 + 20**2 + 10**2) ** 0.5, rel=0.02)
    assert box["centroid"] == pytest.approx([0.3, 0, 0], abs=1e-4)
    for name in ("Sphere A", "Sphere B"):
        sphere = metrics["layers"][name]
        assert sphere["dims_mm"] == pytest.approx([20, 20, 20], rel=0.02)
        assert sphere["max_diameter_mm"] == pytest.approx(20, rel=0.02)

    # The far-away box (>50 mm) is not paired with anything.
    assert len(metrics["pairs"]) == 1
    pair = metrics["pairs"][0]
    assert {pair["a"], pair["b"]} == {"Sphere A", "Sphere B"}
    assert pair["min_distance_mm"] == pytest.approx(20, rel=0.02)
    point_a, point_b = (pair["point_a"], pair["point_b"]) if pair["a"] == "Sphere A" else (pair["point_b"], pair["point_a"])
    assert (sum(c * c for c in point_a)) ** 0.5 == pytest.approx(10 * MM, rel=0.02)
    assert (sum((c - o) ** 2 for c, o in zip(point_b, (0.04, 0, 0)))) ** 0.5 == pytest.approx(10 * MM, rel=0.02)
    assert point_a[0] < point_b[0]


def test_instance_transforms_are_applied(tmp_path):
    scene = trimesh.Scene()
    bolt = trimesh.creation.box(extents=(4 * MM, 4 * MM, 4 * MM))
    bolt.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(name="m", baseColorFactor=[0.5] * 3 + [1]))
    scene.add_geometry(bolt, node_name="Bolt-1", geom_name="bolt", transform=trimesh.transformations.translation_matrix((0, 0, 0)))
    scene.add_geometry(bolt, node_name="Bolt-2", geom_name="bolt", transform=trimesh.transformations.translation_matrix((0.03, 0, 0)))
    plate = trimesh.creation.box(extents=(60 * MM, 20 * MM, 2 * MM))
    plate.apply_translation((0.015, 0, -0.01))
    plate.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(name="p", baseColorFactor=[0.2] * 3 + [1]))
    scene.add_geometry(plate, node_name="Plate", geom_name="plate")
    glb = tmp_path / "inst.glb"
    scene.export(str(glb))
    layers = normalize_layers(str(glb))
    assert {layer["name"]: layer.get("count") for layer in layers} == {"Bolt": 2, "Plate": None}

    metrics = compute_layer_metrics(str(glb), layers)

    # Two bolts 30 mm apart (4 mm wide) form one layer 34 mm long.
    assert metrics["layers"]["Bolt"]["dims_mm"][0] == pytest.approx(34, rel=0.02)
    assert metrics["layers"]["Bolt"]["centroid"][0] == pytest.approx(0.015, abs=1e-4)
    # Bolt bottoms at z=-2 mm, plate top at z=-9 mm: 7 mm gap.
    assert metrics["pairs"][0]["min_distance_mm"] == pytest.approx(7, rel=0.05)


def test_sixty_four_layers_stay_within_the_budget(tmp_path):
    # A row of small parts 30 mm apart: each one has only its neighbours in range.
    parts = [(f"Part {i}", _sphere(i * 30, r_mm=4, subdivisions=3)) for i in range(64)]
    glb = tmp_path / "many.glb"
    layers = _layered_glb(glb, parts)
    assert len(layers) == 64

    started = time.monotonic()
    metrics = compute_layer_metrics(str(glb), layers)
    elapsed = time.monotonic() - started

    assert metrics is not None and len(metrics["layers"]) == 64
    assert elapsed < layer_metrics.METRIC_TIME_BUDGET_S, elapsed
    assert "truncated" not in metrics
    # Neighbours are 30 - 8 = 22 mm apart; the next-but-one are 52 mm apart.
    assert len(metrics["pairs"]) == 63
    assert all(p["min_distance_mm"] == pytest.approx(22, rel=0.05) for p in metrics["pairs"])


def test_dense_arrangement_is_capped_and_marked_truncated(tmp_path, monkeypatch):
    # This checks the pair cap, not the time budget: a loaded CI machine (or
    # pytest -n 8) must not stop the pairing early and leave fewer pairs.
    monkeypatch.setattr(layer_metrics, "METRIC_TIME_BUDGET_S", 600.0)
    parts = [(f"Part {i}", _sphere((i % 8) * 10, r_mm=2, subdivisions=2)) for i in range(64)]
    for i, (_, mesh) in enumerate(parts):
        mesh.apply_translation((0, (i // 8) * 0.01, 0))
    glb = tmp_path / "dense.glb"
    layers = _layered_glb(glb, parts)

    metrics = compute_layer_metrics(str(glb), layers)

    assert len(metrics["pairs"]) == layer_metrics.METRIC_MAX_PAIRS
    assert metrics["truncated"] is True
    # Closest pairs come first, so the cut keeps the most relevant ones.
    distances = [p["min_distance_mm"] for p in metrics["pairs"]]
    assert distances == sorted(distances) or distances[0] <= distances[-1]


def test_time_budget_stops_pairing_and_flags_truncation(tmp_path, monkeypatch):
    glb = tmp_path / "m.glb"
    layers = _two_spheres_and_box(glb)
    monkeypatch.setattr(layer_metrics, "METRIC_TIME_BUDGET_S", -1.0)

    metrics = compute_layer_metrics(str(glb), layers)

    assert metrics["pairs"] == [] and metrics["truncated"] is True
    assert len(metrics["layers"]) == 3


def test_touching_layers_report_zero_distance(tmp_path):
    glb = tmp_path / "touch.glb"
    layers = _layered_glb(glb, [("Left", _sphere(0)), ("Right", _sphere(20))])
    assert compute_layer_metrics(str(glb), layers)["pairs"][0]["min_distance_mm"] == pytest.approx(0, abs=0.5)


def test_failures_return_none(tmp_path):
    layers = [{"name": "A", "materials": ["A"]}, {"name": "B", "materials": ["B"]}]
    assert compute_layer_metrics(str(tmp_path / "missing.glb"), layers) is None
    bad = tmp_path / "bad.glb"
    bad.write_bytes(b"not a glb")
    assert compute_layer_metrics(str(bad), layers) is None
    glb = tmp_path / "m.glb"
    _two_spheres_and_box(glb)
    assert compute_layer_metrics(str(glb), [{"name": "Nope", "materials": ["nope"]}]) is None
    assert compute_layer_metrics(str(glb), None) is None


def test_restrict_and_rename_helpers():
    metrics = {
        "version": 1, "unit": "mm",
        "layers": {"A": {"dims_mm": [1, 1, 1]}, "B": {"dims_mm": [2, 2, 2]}, "C": {"dims_mm": [3, 3, 3]}},
        "pairs": [{"a": "A", "b": "B", "min_distance_mm": 1.0}, {"a": "B", "b": "C", "min_distance_mm": 2.0}],
    }
    kept = layer_metrics.restrict_metrics(metrics, ["A", "B"])
    assert set(kept["layers"]) == {"A", "B"} and [(p["a"], p["b"]) for p in kept["pairs"]] == [("A", "B")]
    assert layer_metrics.restrict_metrics(metrics, ["X"]) is None
    swapped = layer_metrics.rename_metrics_layers(metrics, {"A": "B", "B": "A"})
    assert swapped["layers"]["B"] == {"dims_mm": [1, 1, 1]} and swapped["layers"]["A"] == {"dims_mm": [2, 2, 2]}
    assert (swapped["pairs"][0]["a"], swapped["pairs"][0]["b"]) == ("B", "A")


# --- pipeline ---------------------------------------------------------------------------------


def test_step_assembly_upload_stores_metrics(client):
    from tests.test_cad_upload_pipeline import _project as cad_project
    from tests.test_cad_upload_pipeline import _upload as cad_upload

    slug = cad_project(client)
    cad_upload(client, slug, "gearbox.step", (FIXTURES / "assembly_colored.step").read_bytes())
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        info = model.layer_info
        assert info["metrics_public"] is True
        assert set(info["metrics"]["layers"]) == {"Housing", "Shaft", "Cap"}
        assert all(len(m["dims_mm"]) == 3 and m["max_diameter_mm"] > 0 for m in info["metrics"]["layers"].values())


def test_segmentation_upload_stores_metrics_and_replace_recomputes_them(client, tmp_path):
    slug = _project(client)
    _upload(client, slug, _labelmap(tmp_path), "seg.nii.gz")
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        info = model.layer_info
        assert info["metrics_public"] is True
        assert set(info["metrics"]["layers"]) == {"Label 1", "Label 2"}
        # Two r=8 mm spheres with centres ~41.6 mm apart: ~25.6 mm between the surfaces.
        (pair,) = info["metrics"]["pairs"]
        assert 20 < pair["min_distance_mm"] < 30
        assert all(layer["dims_mm"][0] == pytest.approx(16, rel=0.15) for layer in info["metrics"]["layers"].values())
        model_id = model.id
        # The owner's choice (a later wave adds the toggle) survives a replacement,
        # and stale measurements are replaced by fresh ones.
        stale = dict(info)
        stale["metrics_public"] = False
        stale["metrics"] = {"version": 1, "unit": "mm", "layers": {"Label 1": {"dims_mm": [1, 1, 1]}}, "pairs": []}
        model.layer_info = stale
        db.session.commit()

    with open(_labelmap(tmp_path), "rb") as handle:
        client.post(
            f"/models/{model_id}/replace",
            data={"file": (handle, "seg.nii.gz"), "compliance_confirm": "yes", "medical_confirm": "yes"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    with client.application.app_context():
        info = db.session.get(Model3D, model_id).layer_info
        assert info["metrics_public"] is False
        assert set(info["metrics"]["layers"]) == {"Label 1", "Label 2"}
        assert info["metrics"]["layers"]["Label 1"]["dims_mm"][0] > 5


def test_metrics_for_layers_that_did_not_survive_are_dropped():
    from app import verified_layer_info

    glb_path = None
    metrics = {"version": 1, "unit": "mm", "layers": {"A": {}, "Ghost": {}}, "pairs": [{"a": "A", "b": "Ghost"}]}
    # No layers: nothing to measure, metrics are not stored.
    assert verified_layer_info(glb_path, [], ["note"], metrics) == {"layers": [], "notes": ["note"]}


# --- backfill job -----------------------------------------------------------------------------


def _make_model(app, *, draco=False, with_metrics=False, email="owner@example.com"):
    with app.app_context():
        user = User.query.filter_by(email=email).first() or create_user(email=email, username="Owner")
        paper = Paper(title="P", slug=f"p-{uuid.uuid4().hex[:6]}", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        mid = str(uuid.uuid4())
        folder = Path(app.config["CONVERTED_FOLDER"]) / mid
        folder.mkdir(parents=True)
        glb = folder / "model.glb"
        layers = _layered_glb(glb, [("Alpha", _sphere(0)), ("Beta", _sphere(40)), ("Gamma", _sphere(500))])
        if draco:
            assert optimize_glb(str(glb), keep_layers=True)
            assert glb_has_draco(str(glb))
            assert read_layers(str(glb))
        info = {"layers": layers, "notes": []}
        if with_metrics:
            info["metrics"] = {"version": 1, "unit": "mm", "layers": {"Alpha": {}}, "pairs": []}
        db.session.add(
            Model3D(
                id=mid, paper_id=paper.id, user_id=user.id, glb_path=str(glb), processing_status="ready",
                license_type="academic", source_format="glb", appearance_color="#cccccc",
                original_filename="m.glb", file_size=glb.stat().st_size,
                access_starts_at=datetime.now(UTC), access_expires_at=datetime.now(UTC) + timedelta(days=365),
                layer_info=info,
            )
        )
        db.session.commit()
        return mid


def _enqueue(app, mid):
    from app import enqueue_conversion_job

    with app.app_context():
        model = db.session.get(Model3D, mid)
        return enqueue_conversion_job(
            app, model=model, job_kwargs={"model_id": mid, "glb_path": model.glb_path}, job_type="layer_metrics"
        ).id


def _assert_backfilled(app, mid, job_id):
    with app.app_context():
        model = db.session.get(Model3D, mid)
        info = model.layer_info
        assert set(info["metrics"]["layers"]) == {"Alpha", "Beta", "Gamma"}
        assert info["metrics_public"] is True
        assert [p["min_distance_mm"] for p in info["metrics"]["pairs"]] == pytest.approx([20], rel=0.02)
        assert [layer["name"] for layer in info["layers"]] == ["Alpha", "Beta", "Gamma"]
        job = db.session.get(ConversionJob, job_id)
        assert job.status == "completed" and job.job_type == "layer_metrics"


def test_backfill_job_fills_metrics_for_an_uncompressed_glb(app):
    mid = _make_model(app)
    _assert_backfilled(app, mid, _enqueue(app, mid))


def test_backfill_job_reads_a_draco_glb_and_leaves_it_untouched(app):
    from converters.glb_optimize import _find_cli

    cli = _find_cli()
    if cli[0] == "npx":
        pytest.skip("gltf-transform is not installed")
    mid = _make_model(app, draco=True)
    with app.app_context():
        glb_path = db.session.get(Model3D, mid).glb_path
    before = Path(glb_path).read_bytes()
    _assert_backfilled(app, mid, _enqueue(app, mid))
    assert Path(glb_path).read_bytes() == before and glb_has_draco(glb_path)


def test_backfill_job_skips_models_without_layers_or_glb(app):
    mid = _make_model(app)
    with app.app_context():
        model = db.session.get(Model3D, mid)
        info = dict(model.layer_info)
        info["layers"] = info["layers"][:1]
        model.layer_info = info
        db.session.commit()
    job_id = _enqueue(app, mid)
    with app.app_context():
        assert "metrics" not in db.session.get(Model3D, mid).layer_info
        assert db.session.get(ConversionJob, job_id).status == "completed"

    mid = _make_model(app)
    with app.app_context():
        Path(db.session.get(Model3D, mid).glb_path).unlink()
    job_id = _enqueue(app, mid)
    with app.app_context():
        assert "metrics" not in db.session.get(Model3D, mid).layer_info
        assert db.session.get(ConversionJob, job_id).status == "completed"


def test_worker_dispatches_layer_metrics_jobs(app):
    from app import run_next_conversion_job

    mid = _make_model(app)
    with app.app_context():
        model = db.session.get(Model3D, mid)
        job = ConversionJob(
            job_type="layer_metrics", status="pending", model_id=mid, user_id=model.user_id,
            payload={"model_id": mid, "glb_path": model.glb_path},
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id
    assert run_next_conversion_job(app) is True
    _assert_backfilled(app, mid, job_id)


def test_exhausted_layer_metrics_job_does_not_fail_the_model(app):
    from app import run_next_conversion_job

    mid = _make_model(app)
    with app.app_context():
        model = db.session.get(Model3D, mid)
        job = ConversionJob(
            job_type="layer_metrics", status="pending", model_id=mid, user_id=model.user_id,
            payload={"model_id": mid, "glb_path": model.glb_path}, attempts=3, max_attempts=3,
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id
    assert run_next_conversion_job(app) is True
    with app.app_context():
        assert db.session.get(ConversionJob, job_id).status == "failed"
        assert db.session.get(Model3D, mid).processing_status == "ready"


# --- layer editor -----------------------------------------------------------------------------


def test_renaming_a_layer_renames_its_metrics(client, app):
    mid = _make_model(app)
    _assert_backfilled(app, mid, _enqueue(app, mid))
    login(client, email="owner@example.com")
    with app.app_context():
        layers = [dict(layer) for layer in db.session.get(Model3D, mid).layer_info["layers"]]
    data = {}
    for i, layer in enumerate(layers):
        data[f"layer_name_{i}"] = {"Alpha": "Liver", "Beta": "Tumour"}.get(layer["name"], layer["name"])
        data[f"layer_color_{i}"] = layer["color"]

    assert client.post(f"/models/{mid}/layers", data=data).status_code == 302

    with app.app_context():
        info = db.session.get(Model3D, mid).layer_info
        assert [layer["name"] for layer in info["layers"]] == ["Liver", "Tumour", "Gamma"]
        assert set(info["metrics"]["layers"]) == {"Liver", "Tumour", "Gamma"}
        (pair,) = info["metrics"]["pairs"]
        assert {pair["a"], pair["b"]} == {"Liver", "Tumour"}
        assert info["metrics_public"] is True


# --- admin trigger ----------------------------------------------------------------------------


def test_admin_backfill_is_admin_only(client, app):
    mid = _make_model(app, email="owner@example.com")
    assert client.post("/admin/layer-metrics/backfill").status_code in (302, 401)
    login(client, email="owner@example.com")
    assert client.post("/admin/layer-metrics/backfill").status_code == 403
    with app.app_context():
        assert "metrics" not in db.session.get(Model3D, mid).layer_info
        assert ConversionJob.query.count() == 0


def test_admin_backfill_queues_models_without_metrics(client, app):
    todo = _make_model(app)
    done = _make_model(app, with_metrics=True)
    busy = _make_model(app)
    with app.app_context():
        db.session.add(ConversionJob(job_type="layer_metrics", status="pending", model_id=busy, payload={}))
        plain = Model3D(
            id=str(uuid.uuid4()), paper_id=db.session.get(Model3D, todo).paper_id, user_id=db.session.get(Model3D, todo).user_id,
            glb_path="x.glb", processing_status="ready", license_type="academic", source_format="glb",
            appearance_color="#cccccc", original_filename="x.glb", file_size=1,
            access_starts_at=datetime.now(UTC), access_expires_at=datetime.now(UTC) + timedelta(days=365),
        )
        db.session.add(plain)
        db.session.commit()
    _make_admin(client)

    html = client.post("/admin/layer-metrics/backfill", follow_redirects=True).get_data(as_text=True)

    assert "Queued layer measurements for 1 model." in html
    with app.app_context():
        assert set(db.session.get(Model3D, todo).layer_info["metrics"]["layers"]) == {"Alpha", "Beta", "Gamma"}
        assert db.session.get(Model3D, done).layer_info["metrics"]["layers"] == {"Alpha": {}}
        assert "metrics" not in db.session.get(Model3D, busy).layer_info
        assert ConversionJob.query.filter_by(job_type="layer_metrics").count() == 2  # busy's + todo's
    html = client.post("/admin/layer-metrics/backfill", follow_redirects=True).get_data(as_text=True)
    assert "No models need layer measurements." in html


def test_admin_storage_page_has_the_backfill_button(client):
    _make_admin(client)
    html = client.get("/admin/storage").get_data(as_text=True)
    assert "Compute layer measurements" in html
    assert "/admin/layer-metrics/backfill" in html


def test_rescaling_a_layered_model_remeasures_it(client):
    from tests.test_cad_upload_pipeline import _project as cad_project
    from tests.test_cad_upload_pipeline import _upload as cad_upload

    slug = cad_project(client)
    cad_upload(client, slug, "gearbox.step", (FIXTURES / "assembly_colored.step").read_bytes())
    with client.application.app_context():
        model = Model3D.query.one()
        model_id = model.id
        before = model.layer_info["metrics"]["layers"]["Shaft"]["max_diameter_mm"]
    client.post(f"/models/{model_id}/rescale", data={"target_longest_cm": "9"}, follow_redirects=True)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        after = model.layer_info["metrics"]["layers"]["Shaft"]["max_diameter_mm"]
        assert model.layer_info["metrics_public"] is True
    assert after == pytest.approx(before * 2, rel=0.03)  # 4.5 cm -> 9 cm
