"""Admin clean-up round 3 (backend): R2 mirror retry runs in the worker, the
sidebar failed-jobs badge only counts recent conversion failures, the last
institution admin cannot be removed, the owner's project delete records the
previous visibility for admin restore, and the admin dashboard no longer
computes values no template renders."""
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from flask import template_rendered

from models import AuditLog, ConversionJob, InstitutionMember, Model3D, Paper, QRLink, User, db
from tests.conftest import create_user, login
from tests.test_institutions import add_member, create_institution


def _make_admin(client, email="admin@example.com"):
    with client.application.app_context():
        admin = create_user(email=email, username="Admin User")
        admin.is_admin = True
        db.session.commit()
    login(client, email=email)


def _seed_model(client, model_id="r3-model-1", owner_email="owner@example.com", **overrides):
    with client.application.app_context():
        owner = User.query.filter_by(email=owner_email).first() or create_user(email=owner_email, username="Owner")
        paper = Paper(title=f"Paper {model_id}", slug=f"paper-{model_id}", user_id=owner.id)
        db.session.add(paper)
        db.session.flush()
        defaults = dict(
            id=model_id, paper_id=paper.id, user_id=owner.id, glb_path="model.glb",
            processing_status="ready", license_type="free",
        )
        defaults.update(overrides)
        db.session.add(Model3D(**defaults))
        db.session.commit()
        return model_id


def _flagged_model(client, **overrides):
    model_id = _seed_model(client, r2_mirror_failed_at=datetime.now(UTC), **overrides)
    os.makedirs(os.path.join(client.application.config["CONVERTED_FOLDER"], model_id), exist_ok=True)
    return model_id


def _add_job(client, model_id, job_type="model_upload", status="failed", finished_days_ago=None):
    with client.application.app_context():
        job = ConversionJob(
            job_type=job_type, status=status, model_id=model_id, attempts=1, payload={"model_id": model_id},
        )
        if finished_days_ago is not None:
            job.finished_at = datetime.now(UTC) - timedelta(days=finished_days_ago)
        db.session.add(job)
        db.session.commit()
        return job.id


# --- 1. mirror retry is a worker job --------------------------------------------

def test_mirror_retry_enqueues_a_worker_job_and_does_not_mirror_in_the_request(client):
    _make_admin(client)
    model_id = _flagged_model(client)
    with patch("app.r2_mirror_enabled", return_value=True), \
            patch("app.mirror_directory_sync", return_value=True) as sync, \
            patch("app.process_r2_mirror_job") as run_job:
        response = client.post(f"/admin/models/{model_id}/mirror/retry", follow_redirects=True)
    assert response.status_code == 200
    assert "queued" in response.get_data(as_text=True).lower()
    assert not sync.called  # the web request only enqueues
    with client.application.app_context():
        jobs = ConversionJob.query.filter_by(job_type="r2_mirror", model_id=model_id).all()
        assert len(jobs) == 1
        assert run_job.call_args.kwargs["job_id"] == jobs[0].id
        # Only a real mirror inside the job clears the flag.
        assert db.session.get(Model3D, model_id).r2_mirror_failed_at is not None


def test_mirror_retry_job_clears_flag_only_on_success(client):
    _make_admin(client)
    model_id = _flagged_model(client)
    with patch("app.r2_mirror_enabled", return_value=True), patch("app.mirror_directory_sync", return_value=True):
        client.post(f"/admin/models/{model_id}/mirror/retry", follow_redirects=True)
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).r2_mirror_failed_at is None
        job = ConversionJob.query.filter_by(job_type="r2_mirror", model_id=model_id).one()
        assert job.status == "completed"
        assert AuditLog.query.filter_by(event_type="admin_model_mirror_retried").count() == 1

    model_id = _flagged_model(client, model_id="r3-model-2")
    with patch("app.r2_mirror_enabled", return_value=True), patch("app.mirror_directory_sync", return_value=False):
        client.post(f"/admin/models/{model_id}/mirror/retry", follow_redirects=True)
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).r2_mirror_failed_at is not None
        job = ConversionJob.query.filter_by(job_type="r2_mirror", model_id=model_id).one()
        assert job.status == "failed"
        assert job.error


def test_mirror_retry_dedupes_a_pending_job(client):
    _make_admin(client)
    model_id = _flagged_model(client)
    _add_job(client, model_id, job_type="r2_mirror", status="pending")
    with patch("app.r2_mirror_enabled", return_value=True), patch("app.mirror_directory_sync") as sync:
        response = client.post(f"/admin/models/{model_id}/mirror/retry", follow_redirects=True)
    assert "already" in response.get_data(as_text=True).lower()
    assert not sync.called
    with client.application.app_context():
        assert ConversionJob.query.filter_by(job_type="r2_mirror", model_id=model_id).count() == 1


def test_mirror_retry_refusals_do_not_enqueue(client):
    _make_admin(client)
    model_id = _flagged_model(client)
    with patch("app.r2_mirror_enabled", return_value=False):
        response = client.post(f"/admin/models/{model_id}/mirror/retry", follow_redirects=True)
    assert "not enabled" in response.get_data(as_text=True)
    missing_id = _seed_model(client, model_id="r3-missing", r2_mirror_failed_at=datetime.now(UTC))
    with patch("app.r2_mirror_enabled", return_value=True):
        response = client.post(f"/admin/models/{missing_id}/mirror/retry", follow_redirects=True)
    assert "missing locally" in response.get_data(as_text=True)
    with client.application.app_context():
        assert ConversionJob.query.filter_by(job_type="r2_mirror").count() == 0


def test_mirror_job_runs_through_the_worker_dispatcher(client):
    from app import run_next_conversion_job

    model_id = _flagged_model(client)
    _add_job(client, model_id, job_type="r2_mirror", status="pending")
    with patch("app.r2_mirror_enabled", return_value=True), patch("app.mirror_directory_sync", return_value=True) as sync:
        assert run_next_conversion_job(client.application) is True
    assert sync.called
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).r2_mirror_failed_at is None
        assert ConversionJob.query.filter_by(job_type="r2_mirror").one().status == "completed"


# --- 2. sidebar failed-jobs badge --------------------------------------------------

def test_failed_badge_ignores_old_and_optional_failures(client):
    _make_admin(client)
    model_id = _seed_model(client)
    _add_job(client, model_id, status="failed", finished_days_ago=30)  # old news
    _add_job(client, model_id, job_type="poster", status="failed", finished_days_ago=1)  # optional
    text = client.get("/admin/users").get_data(as_text=True)
    assert "admin-sidenav-alert" not in text
    overview = client.get("/admin").get_data(as_text=True)
    assert "failed conversion job" not in overview

    _add_job(client, model_id, job_type="model_replace", status="failed", finished_days_ago=2)
    text = client.get("/admin/users").get_data(as_text=True)
    assert 'class="admin-sidenav-alert" title="Failed jobs">1</span>' in text
    overview = client.get("/admin").get_data(as_text=True)
    assert "1 failed conversion job" in overview


# --- 3. last institution admin ------------------------------------------------------

def test_platform_admin_cannot_remove_the_last_institution_admin(client):
    _make_admin(client)
    with client.application.app_context():
        institution = create_institution()
        inst_admin = create_user(email="inst-admin@example.com", username="Inst Admin")
        member = create_user(email="inst-member@example.com", username="Inst Member")
        admin_membership = add_member(institution, inst_admin, role="admin")
        member_membership = add_member(institution, member)
        institution_id, admin_mid, member_mid = institution.id, admin_membership.id, member_membership.id

    response = client.post(
        f"/admin/institutions/{institution_id}/members/{admin_mid}/remove", follow_redirects=True
    )
    assert "last institution admin" in response.get_data(as_text=True)
    with client.application.app_context():
        assert db.session.get(InstitutionMember, admin_mid) is not None
        assert AuditLog.query.filter_by(event_type="institution_member_removed").count() == 0

    # A plain member can still be removed.
    client.post(f"/admin/institutions/{institution_id}/members/{member_mid}/remove", follow_redirects=True)
    with client.application.app_context():
        assert db.session.get(InstitutionMember, member_mid) is None

    # And an admin can be removed once another admin exists.
    with client.application.app_context():
        second = create_user(email="inst-admin2@example.com", username="Inst Admin 2")
        add_member(db.session.get(institution.__class__, institution_id), second, role="admin")
    client.post(f"/admin/institutions/{institution_id}/members/{admin_mid}/remove", follow_redirects=True)
    with client.application.app_context():
        assert db.session.get(InstitutionMember, admin_mid) is None


# --- 4. owner delete keeps restore-able visibility -------------------------------

def test_owner_delete_goes_private_and_admin_restore_brings_visibility_back(client):
    with client.application.app_context():
        owner = create_user(email="owner@example.com", username="Owner")
        paper = Paper(
            title="Owner Proj", slug="owner-proj", user_id=owner.id,
            visibility="unlisted", is_public=False, share_token="tok-owner-123",
        )
        db.session.add(paper)
        db.session.flush()
        model = Model3D(id="r3-owner-model", paper_id=paper.id, user_id=owner.id,
                        glb_path="model.glb", processing_status="ready", license_type="free")
        db.session.add(model)
        db.session.add(QRLink(public_id="r3ownerqr", model_id=model.id))
        db.session.commit()
        paper_id = paper.id

    login(client, email="owner@example.com")
    client.post("/papers/owner-proj/delete", follow_redirects=True)
    with client.application.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.status == "deleted"
        assert paper.visibility == "private" and paper.is_public is False
        event = AuditLog.query.filter_by(event_type="paper_deleted", resource_id=str(paper_id)).one()
        assert event.details["visibility"] == "unlisted"
    # Deleted projects keep failing gracefully (404, never a dead viewer).
    assert client.get("/m/r3ownerqr").status_code == 404
    assert client.get("/p/owner-proj").status_code == 404
    client.post("/auth/logout")

    _make_admin(client)
    html = client.post(f"/admin/papers/{paper_id}/restore", follow_redirects=True).get_data(as_text=True)
    assert "Review link" in html
    with client.application.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.status == "active" and paper.visibility == "unlisted"
        assert paper.share_token


def test_admin_restore_uses_the_latest_delete_event(client):
    """An owner delete after an older admin delete/restore cycle must restore
    the owner's last visibility, not the stale admin event."""
    with client.application.app_context():
        owner = create_user(email="owner@example.com", username="Owner")
        paper = Paper(title="Twice", slug="twice", user_id=owner.id, visibility="public", is_public=True)
        db.session.add(paper)
        db.session.commit()
        paper_id = paper.id
    _make_admin(client)
    client.post(f"/admin/papers/{paper_id}/visibility", data={"visibility": "public", "status": "deleted"},
                follow_redirects=True)
    client.post(f"/admin/papers/{paper_id}/restore", follow_redirects=True)
    with client.application.app_context():
        paper = db.session.get(Paper, paper_id)
        paper.visibility, paper.is_public = "private", False
        db.session.commit()
    client.post("/auth/logout")
    login(client, email="owner@example.com")
    client.post("/papers/twice/delete", follow_redirects=True)
    client.post("/auth/logout")
    login(client, email="admin@example.com")
    client.post(f"/admin/papers/{paper_id}/restore", follow_redirects=True)
    with client.application.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.status == "active"
        assert paper.visibility == "private" and paper.is_public is False


# --- 5. dashboard computes only what templates render ---------------------------

def _captured_context(client, path):
    captured = {}

    def record(sender, template, context, **extra):
        captured.update(context)

    template_rendered.connect(record, client.application)
    try:
        assert client.get(path).status_code == 200
    finally:
        template_rendered.disconnect(record, client.application)
    return captured


def test_content_page_skips_the_paper_aggregate_and_dropped_kwargs_are_gone(client):
    _make_admin(client)
    _seed_model(client)
    content = _captured_context(client, "/admin/content")
    assert content["totals"]["papers"] == 0  # not computed for the content page
    for key in ("license_counts", "source_format_counts", "job_counts"):
        assert key not in content
    overview = _captured_context(client, "/admin")
    assert overview["totals"]["papers"] == 1
    assert overview["stats"]["new_papers_30d"] == 1
