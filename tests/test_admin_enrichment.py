"""Tests for the second-round admin panel enrichment (Faz A / Faz B):
rate-limit audit logging, model version history, poster regeneration, job
payload/max_attempts editing, the publication field allowlist, annotation
moderation, appearance overrides, system health, R2 mirror retry, and CSV
export."""
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from models import AuditLog, ConversionJob, Model3D, ModelAnnotation, Paper, User, db
from tests.conftest import create_user, login


def _make_admin(client, email="admin@example.com"):
    with client.application.app_context():
        admin = create_user(email=email, username="Admin User")
        admin.is_admin = True
        db.session.commit()
    login(client, email=email)


def _seed_model(client, **overrides):
    with client.application.app_context():
        owner = create_user(email="owner@example.com", username="Model Owner")
        paper = Paper(title="Model Paper", slug="model-paper", user_id=owner.id)
        db.session.add(paper)
        db.session.flush()
        defaults = dict(
            id="enrich-model-1",
            paper_id=paper.id,
            user_id=owner.id,
            glb_path="model.glb",
            processing_status="ready",
            anonymization_confirmed=True,
            rights_confirmed=True,
            ethics_responsibility_confirmed=True,
            consent_ip="127.0.0.1",
        )
        defaults.update(overrides)
        model = Model3D(**defaults)
        db.session.add(model)
        db.session.commit()
        return model.id


def test_rate_limit_exceeded_logs_audit_event(client):
    with client.application.app_context():
        create_user()
    # Register hits a tight per-minute limit; blow through it.
    for _ in range(5):
        client.post(
            "/auth/register",
            data={"username": "x", "email": "x@example.com", "password": "password123", "confirm": "password123"},
        )
    with client.application.app_context():
        assert AuditLog.query.filter_by(event_type="rate_limit_exceeded").count() >= 1


def test_academic_field_allowlist_rejects_unknown_value(client):
    with client.application.app_context():
        create_user()
    login(client)
    response = client.post(
        "/papers/new",
        data={"title": "A Paper", "field": "Not A Real Field", "visibility": "public"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        assert Paper.query.filter_by(title="A Paper").count() == 0


def test_academic_field_allowlist_accepts_known_value(client):
    with client.application.app_context():
        create_user()
    login(client)
    response = client.post(
        "/papers/new",
        data={"title": "A Chemistry Paper", "field": "Chemistry", "visibility": "public"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        paper = Paper.query.filter_by(title="A Chemistry Paper").one()
        assert paper.field == "Chemistry"


def test_model_versions_page_renders_for_admin(client):
    _make_admin(client)
    model_id = _seed_model(client)
    response = client.get(f"/admin/models/{model_id}/versions")
    assert response.status_code == 200
    assert "Version history" in response.get_data(as_text=True)


def test_model_versions_page_forbidden_for_member(client):
    with client.application.app_context():
        create_user()
    login(client)
    model_id = _seed_model(client)
    assert client.get(f"/admin/models/{model_id}/versions").status_code == 403


def test_models_page_shows_compliance_record(client):
    _make_admin(client)
    _seed_model(client)
    text = client.get("/admin/models").get_data(as_text=True)
    assert "Compliance record" in text
    assert "127.0.0.1" in text


def test_admin_poster_regenerate_success(client, tmp_path):
    import trimesh

    glb_path = str(tmp_path / "model.glb")
    trimesh.Scene([trimesh.creation.box(extents=[1, 1, 1])]).export(glb_path)
    _make_admin(client)
    model_id = _seed_model(client, glb_path=glb_path)

    with patch("app.mirror_file"), patch("app.ensure_local"):
        response = client.post(f"/admin/models/{model_id}/poster/regenerate", follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.poster_path
        import os
        assert os.path.isfile(model.poster_path)


def test_admin_poster_regenerate_missing_glb_fails_gracefully(client):
    _make_admin(client)
    model_id = _seed_model(client, glb_path="/nonexistent/model.glb")
    with patch("app.mirror_file"), patch("app.ensure_local"):
        response = client.post(f"/admin/models/{model_id}/poster/regenerate", follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).poster_path is None


def _seed_job(client, **overrides):
    model_id = _seed_model(client)
    with client.application.app_context():
        defaults = dict(job_type="model_upload", status="failed", model_id=model_id, max_attempts=3, payload={"is_replacement": False})
        defaults.update(overrides)
        job = ConversionJob(**defaults)
        db.session.add(job)
        db.session.commit()
        return job.id


def test_job_max_attempts_update(client):
    _make_admin(client)
    job_id = _seed_job(client)
    response = client.post(f"/admin/jobs/{job_id}/max-attempts", data={"max_attempts": "7"}, follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        job = db.session.get(ConversionJob, job_id)
        assert job.max_attempts == 7
        assert AuditLog.query.filter_by(event_type="admin_job_max_attempts_changed").count() == 1


def test_job_max_attempts_rejects_out_of_range(client):
    _make_admin(client)
    job_id = _seed_job(client)
    client.post(f"/admin/jobs/{job_id}/max-attempts", data={"max_attempts": "0"}, follow_redirects=True)
    client.post(f"/admin/jobs/{job_id}/max-attempts", data={"max_attempts": "999"}, follow_redirects=True)
    with client.application.app_context():
        assert db.session.get(ConversionJob, job_id).max_attempts == 3


def test_jobs_page_shows_payload(client):
    _make_admin(client)
    _seed_job(client, payload={"is_replacement": True, "note": "hello-payload-marker"})
    text = client.get("/admin/jobs").get_data(as_text=True)
    assert "hello-payload-marker" in text


@pytest.mark.parametrize("path,data", [
    ("/admin/models/x/poster/regenerate", {}),
    ("/admin/jobs/1/max-attempts", {"max_attempts": "5"}),
])
def test_new_faz_a_post_routes_forbidden_for_member(client, path, data):
    with client.application.app_context():
        create_user()
    login(client)
    assert client.post(path, data=data).status_code == 403


# ---------------------------------------------------------------------------
# Faz B: annotation moderation, appearance override, system health,
# R2 mirror retry, CSV export.
# ---------------------------------------------------------------------------


def _seed_annotation(client, model_id, **overrides):
    with client.application.app_context():
        defaults = dict(model_id=model_id, position_x=0, position_y=0, position_z=0, label="Bad label")
        defaults.update(overrides)
        annotation = ModelAnnotation(**defaults)
        db.session.add(annotation)
        db.session.commit()
        return annotation.id


def test_annotations_page_lists_and_forbidden_for_member(client):
    _make_admin(client)
    model_id = _seed_model(client)
    _seed_annotation(client, model_id, label="Spam hotspot")
    text = client.get("/admin/annotations").get_data(as_text=True)
    assert "Spam hotspot" in text

    client.post("/auth/logout")
    with client.application.app_context():
        create_user(email="member2@example.com")
    login(client, email="member2@example.com")
    assert client.get("/admin/annotations").status_code == 403


def test_admin_can_delete_annotation_bypassing_ownership(client):
    _make_admin(client)
    model_id = _seed_model(client)
    annotation_id = _seed_annotation(client, model_id, label="Abusive content")
    response = client.post(f"/admin/annotations/{annotation_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        assert db.session.get(ModelAnnotation, annotation_id) is None
        assert AuditLog.query.filter_by(event_type="admin_annotation_deleted").count() == 1


def test_annotation_delete_forbidden_for_member(client):
    with client.application.app_context():
        create_user()
    login(client)
    model_id = _seed_model(client)
    annotation_id = _seed_annotation(client, model_id)
    assert client.post(f"/admin/annotations/{annotation_id}/delete").status_code == 403


def test_admin_appearance_override_updates_model(client):
    import trimesh

    _make_admin(client)
    import tempfile
    tmp_dir = tempfile.mkdtemp()
    glb_path = f"{tmp_dir}/model.glb"
    trimesh.Scene([trimesh.creation.box(extents=[1, 1, 1])]).export(glb_path)
    model_id = _seed_model(client, glb_path=glb_path)

    with patch("app.mirror_file"), patch("app.ensure_local"):
        response = client.post(
            f"/admin/models/{model_id}/appearance",
            data={"color": "#336699", "roughness": "0.6", "metallic": "0.1", "ar_placement": "wall", "display_name": "Renamed"},
            follow_redirects=True,
        )
    assert response.status_code == 200
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.appearance_color == "#336699"
        assert model.ar_placement == "wall"
        assert model.display_name == "Renamed"
        assert AuditLog.query.filter_by(event_type="admin_model_appearance_changed").count() == 1


def test_admin_appearance_override_rejects_bad_color(client):
    _make_admin(client)
    model_id = _seed_model(client)
    response = client.post(
        f"/admin/models/{model_id}/appearance",
        data={"color": "not-a-color"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        assert AuditLog.query.filter_by(event_type="admin_model_appearance_changed").count() == 0


def test_appearance_override_forbidden_for_member(client):
    with client.application.app_context():
        create_user()
    login(client)
    model_id = _seed_model(client)
    assert client.post(f"/admin/models/{model_id}/appearance", data={"color": "#ffffff"}).status_code == 403


def test_system_health_page_renders_for_admin(client):
    _make_admin(client)
    response = client.get("/admin/system")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "gltf-transform" in text
    assert "Pending jobs" in text


def test_system_health_forbidden_for_member(client):
    with client.application.app_context():
        create_user()
    login(client)
    assert client.get("/admin/system").status_code == 403


def test_storage_page_shows_mirror_failures_and_retry_succeeds(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        model.r2_mirror_failed_at = datetime.now(UTC)
        db.session.commit()

    text = client.get("/admin/storage").get_data(as_text=True)
    assert "R2 mirror failures" in text

    with patch("app.mirror_directory_sync", return_value=True) as mock_mirror:
        response = client.post(f"/admin/models/{model_id}/mirror/retry", follow_redirects=True)
    assert response.status_code == 200
    assert mock_mirror.called
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).r2_mirror_failed_at is None
        assert AuditLog.query.filter_by(event_type="admin_model_mirror_retried").count() == 1


def test_mirror_retry_forbidden_for_member(client):
    with client.application.app_context():
        create_user()
    login(client)
    model_id = _seed_model(client)
    assert client.post(f"/admin/models/{model_id}/mirror/retry").status_code == 403


def test_csv_exports_return_csv_and_forbidden_for_member(client):
    _make_admin(client)
    _seed_model(client)
    for path in ["/admin/users/export.csv", "/admin/content/export.csv", "/admin/revenue/export.csv", "/admin/logs/export.csv"]:
        response = client.get(path)
        assert response.status_code == 200
        assert response.mimetype == "text/csv"

    client.post("/auth/logout")
    with client.application.app_context():
        create_user(email="member3@example.com")
    login(client, email="member3@example.com")
    for path in ["/admin/users/export.csv", "/admin/content/export.csv", "/admin/revenue/export.csv", "/admin/logs/export.csv"]:
        assert client.get(path).status_code == 403


def test_users_csv_export_contains_expected_rows(client):
    _make_admin(client)
    with client.application.app_context():
        create_user(email="csvcheck@example.com", username="CSV Check")
    response = client.get("/admin/users/export.csv")
    body = response.get_data(as_text=True)
    assert "csvcheck@example.com" in body
    assert "email" in body.splitlines()[0]  # header row
