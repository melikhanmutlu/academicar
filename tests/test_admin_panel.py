"""Tests for the redesigned admin panel: sidebar shell, pagination, conversion
job actions, extended paper/model/payment powers, and user email/deactivation."""
from datetime import UTC, datetime

import pytest

from models import (
    AuditLog,
    ConversionJob,
    Model3D,
    Paper,
    Payment,
    QRLink,
    User,
    db,
)
from tests.conftest import create_user, login


def _make_admin(client, email="admin@example.com"):
    with client.application.app_context():
        admin = create_user(email=email, username="Admin User")
        admin.is_admin = True
        db.session.commit()
    login(client, email=email)


ADMIN_PAGES = [
    "/admin",
    "/admin/content",
    "/admin/models",
    "/admin/jobs",
    "/admin/access",
    "/admin/revenue",
    "/admin/security",
    "/admin/storage",
    "/admin/users",
    "/admin/logs",
    "/admin/backups",
    "/admin/blog",
]


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_render_for_admin(client, path):
    _make_admin(client)
    response = client.get(path)
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    # Sidebar shell is present on every admin page.
    assert "Back to site dashboard" in text
    assert "Conversion jobs" in text


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_admin_pages_forbidden_for_member(client, path):
    with client.application.app_context():
        create_user()
    login(client)
    assert client.get(path).status_code == 403


def test_sidebar_marks_active_page(client):
    _make_admin(client)
    response = client.get("/admin/users")
    assert 'aria-current="page"' in response.get_data(as_text=True)


def test_users_pagination(client):
    _make_admin(client)
    with client.application.app_context():
        for i in range(60):
            create_user(email=f"member{i}@example.com", username=f"Member {i}")

    page1 = client.get("/admin/users").get_data(as_text=True)
    # 50 per page; page 1 shows the pagination control and links to page 2.
    assert "page=2" in page1
    # A far page renders without error_out (empty rows, still 200).
    assert client.get("/admin/users?page=999").status_code == 200
    # Filters survive into the pagination links.
    page1_filtered = client.get("/admin/users?user_q=member").get_data(as_text=True)
    assert "user_q=member" in page1_filtered


def _seed_job(client, status="failed", attempts=3, is_replacement=False, model_status="failed"):
    with client.application.app_context():
        owner = create_user(email="jobowner@example.com", username="Job Owner")
        paper = Paper(title="Job Paper", slug="job-paper", user_id=owner.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="job-model-1",
            paper_id=paper.id,
            user_id=owner.id,
            glb_path="model.glb",
            processing_status=model_status,
        )
        db.session.add(model)
        job = ConversionJob(
            job_type="model_upload",
            status=status,
            model_id=model.id,
            attempts=attempts,
            error="boom",
            payload={"is_replacement": is_replacement},
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        db.session.add(job)
        db.session.commit()
        return job.id


def test_job_retry_resets_row_and_requeues_model(client):
    _make_admin(client)
    job_id = _seed_job(client, status="failed", attempts=3, model_status="failed")

    response = client.post(f"/admin/jobs/{job_id}/retry", follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        job = db.session.get(ConversionJob, job_id)
        assert job.status == "pending"
        assert job.attempts == 0
        assert job.error is None
        assert job.finished_at is None
        model = db.session.get(Model3D, job.model_id)
        assert model.processing_status == "queued"
        assert AuditLog.query.filter_by(event_type="admin_job_retried").count() == 1


def test_job_retry_rejected_for_pending(client):
    _make_admin(client)
    job_id = _seed_job(client, status="pending", attempts=0, model_status="queued")
    client.post(f"/admin/jobs/{job_id}/retry", follow_redirects=True)
    with client.application.app_context():
        job = db.session.get(ConversionJob, job_id)
        assert job.status == "pending"  # unchanged
        assert AuditLog.query.filter_by(event_type="admin_job_retried").count() == 0


def test_job_cancel_marks_cancelled_and_fails_model(client):
    _make_admin(client)
    job_id = _seed_job(client, status="pending", attempts=0, model_status="queued")

    client.post(f"/admin/jobs/{job_id}/cancel", follow_redirects=True)
    with client.application.app_context():
        job = db.session.get(ConversionJob, job_id)
        assert job.status == "cancelled"
        model = db.session.get(Model3D, job.model_id)
        assert model.processing_status == "failed"
        assert AuditLog.query.filter_by(event_type="admin_job_cancelled").count() == 1


def test_cancelled_job_is_inert_to_worker(client):
    _make_admin(client)
    job_id = _seed_job(client, status="pending", attempts=0, model_status="queued")
    client.post(f"/admin/jobs/{job_id}/cancel", follow_redirects=True)
    # run_next_conversion_job only claims status == "pending"; a cancelled row
    # must never be picked up.
    from app import run_next_conversion_job

    with client.application.app_context():
        result = run_next_conversion_job(client.application)
        assert result is None or result is False
        job = db.session.get(ConversionJob, job_id)
        assert job.status == "cancelled"


def _seed_paper(client):
    with client.application.app_context():
        owner = create_user(email="powner@example.com", username="Paper Owner")
        paper = Paper(title="Original Title", slug="original-slug", user_id=owner.id, year=2000)
        db.session.add(paper)
        db.session.commit()
        return paper.id


def test_paper_metadata_update_keeps_slug(client):
    _make_admin(client)
    paper_id = _seed_paper(client)
    response = client.post(
        f"/admin/papers/{paper_id}/metadata",
        data={"title": "New Title", "authors": "Doe, J.", "year": "2021", "doi": "10.1/x"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.title == "New Title"
        assert paper.authors == "Doe, J."
        assert paper.year == 2021
        assert paper.slug == "original-slug"  # never editable
        assert AuditLog.query.filter_by(event_type="admin_paper_metadata_changed").count() == 1


def test_paper_metadata_rejects_bad_year(client):
    _make_admin(client)
    paper_id = _seed_paper(client)
    client.post(
        f"/admin/papers/{paper_id}/metadata",
        data={"title": "Keep", "year": "abcd"},
        follow_redirects=True,
    )
    with client.application.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.title == "Original Title"  # unchanged
        assert paper.year == 2000


def _seed_model(client, license_type="academic"):
    with client.application.app_context():
        owner = create_user(email="mowner@example.com", username="Model Owner")
        paper = Paper(title="Model Paper", slug="model-paper", user_id=owner.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="win-model-1",
            paper_id=paper.id,
            user_id=owner.id,
            glb_path="model.glb",
            license_type=license_type,
            storage_limit_bytes=12345,
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()
        return model.id


def test_model_access_window_update_preserves_license(client):
    _make_admin(client)
    model_id = _seed_model(client, license_type="academic")
    response = client.post(
        f"/admin/models/{model_id}/access-window",
        data={"access_starts_at": "2026-01-01T00:00", "access_expires_at": "2027-01-01T00:00"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.access_starts_at.year == 2026
        assert model.access_expires_at.year == 2027
        assert model.license_type == "academic"  # untouched
        assert model.storage_limit_bytes == 12345
        assert AuditLog.query.filter_by(event_type="admin_model_access_window_changed").count() == 1


def test_model_access_window_rejects_reversed_dates(client):
    _make_admin(client)
    model_id = _seed_model(client)
    client.post(
        f"/admin/models/{model_id}/access-window",
        data={"access_starts_at": "2027-01-01T00:00", "access_expires_at": "2026-01-01T00:00"},
        follow_redirects=True,
    )
    with client.application.app_context():
        assert AuditLog.query.filter_by(event_type="admin_model_access_window_changed").count() == 0


def test_qr_regenerate_preserves_public_id(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        model.public_id = "stable-pub-id"
        db.session.add(QRLink(public_id="stable-pub-id", model_id=model.id, target_type="model_viewer"))
        db.session.commit()

    response = client.post(
        f"/admin/models/{model_id}/qr/regenerate",
        data={"next": "user_detail"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.public_id == "stable-pub-id"  # invariant
        assert model.qr_code_path
        assert AuditLog.query.filter_by(event_type="admin_model_qr_regenerated").count() == 1


def test_manual_payment_paid_grants_license(client):
    _make_admin(client)
    model_id = _seed_model(client, license_type="free")
    with client.application.app_context():
        owner = User.query.filter_by(email="mowner@example.com").one()
        owner_email = owner.email

    response = client.post(
        "/admin/payments/create",
        data={
            "user_email": owner_email,
            "model_id": model_id,
            "plan_key": "academic",
            "amount": "990.00",
            "status": "paid",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        payment = Payment.query.filter_by(provider="manual").one()
        assert payment.status == "paid"
        assert payment.paid_at is not None
        assert payment.invoice_number
        model = db.session.get(Model3D, model_id)
        assert model.license_type == "academic"  # license granted via shared helper
        assert AuditLog.query.filter_by(event_type="admin_payment_created").count() == 1


def test_manual_payment_rejects_unknown_user(client):
    _make_admin(client)
    response = client.post(
        "/admin/payments/create",
        data={"user_email": "nobody@example.com", "amount": "10", "status": "pending"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        assert Payment.query.count() == 0


def _seed_member(client, email="target@example.com", google_id=None):
    with client.application.app_context():
        member = create_user(email=email, username="Target User")
        if google_id:
            member.google_id = google_id
            db.session.commit()
        return member.id


def test_user_email_update(client):
    _make_admin(client)
    member_id = _seed_member(client)
    response = client.post(
        f"/admin/users/{member_id}/email",
        data={"email": "renamed@example.com"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        member = db.session.get(User, member_id)
        assert member.email == "renamed@example.com"
        assert AuditLog.query.filter_by(event_type="admin_user_email_changed").count() == 1


def test_user_email_update_rejects_duplicate(client):
    _make_admin(client)
    member_id = _seed_member(client, email="target@example.com")
    with client.application.app_context():
        create_user(email="taken@example.com", username="Other")

    client.post(
        f"/admin/users/{member_id}/email",
        data={"email": "taken@example.com"},
        follow_redirects=True,
    )
    with client.application.app_context():
        member = db.session.get(User, member_id)
        assert member.email == "target@example.com"  # unchanged


def test_user_email_update_rejects_google_account(client):
    _make_admin(client)
    member_id = _seed_member(client, email="g@example.com", google_id="google-123")
    client.post(
        f"/admin/users/{member_id}/email",
        data={"email": "new@example.com"},
        follow_redirects=True,
    )
    with client.application.app_context():
        member = db.session.get(User, member_id)
        assert member.email == "g@example.com"  # unchanged


def test_user_deactivation_blocks_login_and_session(client):
    _make_admin(client)
    member_id = _seed_member(client, email="deact@example.com")

    client.post(f"/admin/users/{member_id}/deactivate", follow_redirects=True)
    with client.application.app_context():
        member = db.session.get(User, member_id)
        assert member.deactivated_at is not None
        assert member.is_active is False

    # Drop the admin session so the deactivated member's own login is tested.
    client.post("/auth/logout", follow_redirects=True)
    # login_user refuses an inactive account, so the member never authenticates
    # and an authenticated page redirects to login instead of rendering.
    login(client, email="deact@example.com", follow_redirects=True)
    assert client.get("/dashboard", follow_redirects=False).status_code == 302

    # Reactivation restores the account (log back in as admin first).
    login(client, email="admin@example.com")
    client.post(f"/admin/users/{member_id}/reactivate", follow_redirects=True)
    with client.application.app_context():
        member = db.session.get(User, member_id)
        assert member.deactivated_at is None


def test_admin_cannot_deactivate_self(client):
    _make_admin(client)
    with client.application.app_context():
        admin = User.query.filter_by(email="admin@example.com").one()
        admin_id = admin.id
    client.post(f"/admin/users/{admin_id}/deactivate", follow_redirects=True)
    with client.application.app_context():
        admin = db.session.get(User, admin_id)
        assert admin.deactivated_at is None  # blocked


NEW_POST_ROUTES = [
    ("/admin/jobs/1/retry", {}),
    ("/admin/jobs/1/cancel", {}),
    ("/admin/papers/1/metadata", {"title": "x"}),
    ("/admin/models/x/access-window", {"access_starts_at": "2026-01-01T00:00"}),
    ("/admin/models/x/qr/regenerate", {}),
    ("/admin/payments/create", {"user_email": "a@b.c", "amount": "1"}),
    ("/admin/users/1/email", {"email": "a@b.c"}),
    ("/admin/users/1/deactivate", {}),
    ("/admin/users/1/reactivate", {}),
]


@pytest.mark.parametrize("path,data", NEW_POST_ROUTES)
def test_new_post_routes_forbidden_for_member(client, path, data):
    with client.application.app_context():
        create_user()
    login(client)
    assert client.post(path, data=data).status_code == 403
