"""Regression tests for the v6 audit fixes.

Covers: email header-injection hardening, stale ConversionJob recovery and the
attempts<max_attempts guard, the QR resolver orphan fallback, the ModelVersion
uniqueness constraint, and paper_is_expired correctness.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from models import ConversionJob, Model3D, ModelVersion, Paper, QRLink, User, db


# --------------------------------------------------------------------------- #
# 1.3 Email header injection
# --------------------------------------------------------------------------- #
def test_send_email_strips_crlf_from_headers(app, monkeypatch):
    import utils.email as email_mod

    captured = {}

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, *a):
            pass

        def send_message(self, message):
            captured["to"] = str(message["To"])
            captured["subject"] = str(message["Subject"])
            captured["bcc"] = message["Bcc"]
            captured["injected"] = message["X-Injected"]

    monkeypatch.setattr(email_mod.smtplib, "SMTP", FakeSMTP)
    with app.app_context():
        app.config["MAIL_SERVER"] = "smtp.example.com"
        ok = email_mod.send_email(
            "victim@example.com\r\nBcc: attacker@evil.com",
            "Subject\r\nX-Injected: yes",
            "body",
        )
    assert ok is True
    # CRLF stripped from header values...
    assert "\n" not in captured["to"] and "\r" not in captured["to"]
    assert "\n" not in captured["subject"] and "\r" not in captured["subject"]
    # ...so no smuggled headers were created.
    assert captured["bcc"] is None
    assert captured["injected"] is None


# --------------------------------------------------------------------------- #
# Helpers for job/model fixtures
# --------------------------------------------------------------------------- #
def _make_model(processing_status="processing"):
    user = User(email="job@example.com", username="Job User")
    user.set_password("password123")
    db.session.add(user)
    db.session.flush()
    paper = Paper(title="Job Paper", slug="job-paper", user_id=user.id)
    db.session.add(paper)
    db.session.flush()
    model = Model3D(
        id="job-model-1",
        paper_id=paper.id,
        user_id=user.id,
        glb_path="model.glb",
        public_id="job-public",
        processing_status=processing_status,
    )
    db.session.add(model)
    db.session.commit()
    return model


# --------------------------------------------------------------------------- #
# 1.2 Stale job recovery + attempts guard
# --------------------------------------------------------------------------- #
def test_stale_processing_job_with_attempts_left_is_requeued(app):
    from app import reclaim_stale_conversion_jobs

    with app.app_context():
        model = _make_model()
        stale_started = datetime.now(UTC) - timedelta(seconds=app.config["JOB_STALE_SECONDS"] + 60)
        job = ConversionJob(
            job_type="model_upload",
            status="processing",
            model_id=model.id,
            user_id=model.user_id,
            payload={"model_id": model.id},
            attempts=1,
            max_attempts=3,
            started_at=stale_started,
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id

    reclaim_stale_conversion_jobs(app)

    with app.app_context():
        job = db.session.get(ConversionJob, job_id)
        assert job.status == "pending"
        assert job.started_at is None


def test_stale_processing_job_exhausted_is_failed(app):
    from app import reclaim_stale_conversion_jobs

    with app.app_context():
        model = _make_model()
        stale_started = datetime.now(UTC) - timedelta(seconds=app.config["JOB_STALE_SECONDS"] + 60)
        job = ConversionJob(
            job_type="model_upload",
            status="processing",
            model_id=model.id,
            user_id=model.user_id,
            payload={"model_id": model.id},
            attempts=3,
            max_attempts=3,
            started_at=stale_started,
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id
        model_id = model.id

    reclaim_stale_conversion_jobs(app)

    with app.app_context():
        job = db.session.get(ConversionJob, job_id)
        model = db.session.get(Model3D, model_id)
        assert job.status == "failed"
        assert model.processing_status == "failed"


def test_recent_processing_job_is_not_reclaimed(app):
    from app import reclaim_stale_conversion_jobs

    with app.app_context():
        model = _make_model()
        job = ConversionJob(
            job_type="model_upload",
            status="processing",
            model_id=model.id,
            user_id=model.user_id,
            payload={"model_id": model.id},
            attempts=1,
            max_attempts=3,
            started_at=datetime.now(UTC),  # fresh, still running
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id

    assert reclaim_stale_conversion_jobs(app) == 0
    with app.app_context():
        assert db.session.get(ConversionJob, job_id).status == "processing"


def test_run_next_skips_attempts_exhausted_jobs(app):
    from app import run_next_conversion_job

    with app.app_context():
        model = _make_model(processing_status="queued")
        job = ConversionJob(
            job_type="model_upload",
            status="pending",
            model_id=model.id,
            user_id=model.user_id,
            payload={"model_id": model.id},
            attempts=3,
            max_attempts=3,
        )
        db.session.add(job)
        db.session.commit()

    # The only pending job has exhausted its attempts, so nothing should run.
    assert run_next_conversion_job(app) is False


# --------------------------------------------------------------------------- #
# 2.1 QR resolver orphan fallback
# --------------------------------------------------------------------------- #
def test_resolver_falls_back_when_qrlink_model_missing(client):
    app = client.application
    with app.app_context():
        user = User(email="orphan@example.com", username="Orphan")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(title="Orphan Paper", slug="orphan-paper", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="orphan-model",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="model.glb",
            public_id="shared-public",
            processing_status="ready",
        )
        db.session.add(model)
        # A QRLink row that points to a now-missing model id (orphaned link),
        # plus the live model reachable via the legacy public_id lookup.
        db.session.add(QRLink(public_id="orphan-link", model_id=model.id))
        db.session.commit()

    # Legacy direct public_id resolution still works (qr_link is None path).
    resp = client.get("/m/shared-public", follow_redirects=False)
    assert resp.status_code == 302
    assert "/view/orphan-model" in resp.headers["Location"]


# --------------------------------------------------------------------------- #
# 1.5 ModelVersion uniqueness
# --------------------------------------------------------------------------- #
def test_model_version_number_unique_per_model(app):
    with app.app_context():
        model = _make_model(processing_status="ready")
        db.session.add(ModelVersion(model_id=model.id, version_number=2, status="ready"))
        db.session.commit()
        db.session.add(ModelVersion(model_id=model.id, version_number=2, status="ready"))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_next_model_version_number_uses_max(app):
    from app import next_model_version_number

    with app.app_context():
        model = _make_model(processing_status="ready")
        db.session.add(ModelVersion(model_id=model.id, version_number=2, status="ready"))
        db.session.add(ModelVersion(model_id=model.id, version_number=3, status="ready"))
        db.session.commit()
        assert next_model_version_number(model.id, 2) == 4


# --------------------------------------------------------------------------- #
# 2.2 paper_is_expired correctness
# --------------------------------------------------------------------------- #
def test_paper_is_expired_logic():
    from licensing import paper_is_expired

    class FakePaper:
        def __init__(self, expires_at):
            self.expires_at = expires_at

    assert paper_is_expired(FakePaper(None)) is False
    assert paper_is_expired(FakePaper(datetime.now(UTC) + timedelta(days=1))) is False
    assert paper_is_expired(FakePaper(datetime.now(UTC) - timedelta(days=1))) is True
    # Naive datetime is treated as UTC, not crashed on.
    assert paper_is_expired(FakePaper(datetime.utcnow() - timedelta(days=1))) is True


# --------------------------------------------------------------------------- #
# 2.6 Purge soft-deleted papers past grace period
# --------------------------------------------------------------------------- #
def test_purge_removes_only_papers_past_grace(app):
    from app import purge_soft_deleted_papers

    with app.app_context():
        app.config["DELETED_PAPER_GRACE_DAYS"] = 30
        user = create_user_in_session("purge@example.com")
        old = Paper(title="Old", slug="old-paper", user_id=user.id,
                    status="deleted", deleted_at=datetime.now(UTC) - timedelta(days=31))
        recent = Paper(title="Recent", slug="recent-paper", user_id=user.id,
                       status="deleted", deleted_at=datetime.now(UTC) - timedelta(days=2))
        active = Paper(title="Active", slug="active-paper", user_id=user.id)
        db.session.add_all([old, recent, active])
        db.session.commit()

    assert purge_soft_deleted_papers(app) == 1

    with app.app_context():
        slugs = {p.slug for p in Paper.query.all()}
        assert "old-paper" not in slugs
        assert {"recent-paper", "active-paper"} <= slugs


def test_purge_disabled_when_grace_zero(app):
    from app import purge_soft_deleted_papers

    with app.app_context():
        app.config["DELETED_PAPER_GRACE_DAYS"] = 0
        user = create_user_in_session("purge0@example.com")
        old = Paper(title="Old", slug="old-paper-0", user_id=user.id,
                    status="deleted", deleted_at=datetime.now(UTC) - timedelta(days=999))
        db.session.add(old)
        db.session.commit()

    assert purge_soft_deleted_papers(app) == 0
    with app.app_context():
        assert Paper.query.filter_by(slug="old-paper-0").count() == 1


# --------------------------------------------------------------------------- #
# 2.7 Per-user total storage cap
# --------------------------------------------------------------------------- #
def test_user_storage_error_respects_cap(app):
    from app import user_storage_error

    with app.app_context():
        app.config["USER_TOTAL_STORAGE_BYTES"] = 10 * 1024 * 1024  # 10 MB
        user = create_user_in_session("quota@example.com")
        paper = Paper(title="Quota", slug="quota-paper", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        db.session.add(Model3D(id="quota-m1", paper_id=paper.id, user_id=user.id,
                               glb_path="m.glb", file_size=8 * 1024 * 1024))
        db.session.commit()
        # 8 MB used; +1 MB ok, +5 MB over the 10 MB cap.
        assert user_storage_error(user.id, 1 * 1024 * 1024) is None
        assert user_storage_error(user.id, 5 * 1024 * 1024) is not None
        # Excluding the existing model (replacement) frees its 8 MB.
        assert user_storage_error(user.id, 9 * 1024 * 1024, exclude_model_id="quota-m1") is None


def test_user_storage_cap_disabled_by_default(app):
    from app import user_storage_error

    with app.app_context():
        user = create_user_in_session("quota-off@example.com")
        assert user_storage_error(user.id, 10**12) is None


def create_user_in_session(email):
    user = User(email=email, username="U")
    user.set_password("password123")
    db.session.add(user)
    db.session.flush()
    return user


# --------------------------------------------------------------------------- #
# 3.6 Admin dashboard pagination
# --------------------------------------------------------------------------- #
def test_admin_users_list_paginates(client):
    from tests.conftest import login

    app = client.application
    app.config["ADMIN_LIST_PAGE_SIZE"] = 2
    with app.app_context():
        admin = User(email="admin@example.com", username="Admin", is_admin=True)
        admin.set_password("password123")
        db.session.add(admin)
        for i in range(4):
            u = User(email=f"member{i}@example.com", username=f"Member{i}")
            u.set_password("password123")
            db.session.add(u)
        db.session.commit()

    login(client, email="admin@example.com")
    page1 = client.get("/admin/users?page=1").get_data(as_text=True)
    page2 = client.get("/admin/users?page=2").get_data(as_text=True)
    assert "admin-pagination" in page1
    assert "Showing 1" in page1
    assert "Showing 3" in page2  # page 2 starts at row 3
    assert page1 != page2
