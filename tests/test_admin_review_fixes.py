"""Regression tests for the admin panel review: payment/licence consistency,
coupon counters, job guards, worker-only heavy work, user controls and the
storage orphan count."""
import os
from datetime import UTC, datetime, timedelta

import pytest

from models import (
    AuditLog,
    ConversionJob,
    Coupon,
    Model3D,
    ModelVersion,
    Paper,
    Payment,
    User,
    db,
)
from tests.conftest import create_user, login

ADMIN_PAGES = [
    "overview", "users", "content", "models", "jobs", "annotations", "access", "revenue", "pricing",
    "security", "storage", "system", "logs", "backups", "blog", "institutions", "analytics",
]


def _make_admin(client, email="admin@example.com"):
    with client.application.app_context():
        admin = create_user(email=email, username="Admin User")
        admin.is_admin = True
        db.session.commit()
    login(client, email=email)


def _seed_model(client, model_id="review-model-1", owner_email="owner@example.com", **overrides):
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


def _seed_payment(client, model_id, status="paid", plan_key="academic", paid_at=None, **extra):
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        payment = Payment(
            user_id=model.user_id, model_id=model_id, plan_key=plan_key, amount_kurus=990,
            currency="USD", provider="manual", status=status, paid_at=paid_at, **extra,
        )
        db.session.add(payment)
        db.session.commit()
        return payment.id


@pytest.mark.parametrize("page", ADMIN_PAGES)
def test_every_admin_page_renders(client, page):
    _make_admin(client)
    _seed_model(client)
    assert client.get(f"/admin/{page}", follow_redirects=True).status_code == 200


def test_resaving_a_paid_payment_does_not_grant_a_fresh_window(client):
    _make_admin(client)
    model_id = _seed_model(client)
    payment_id = _seed_payment(client, model_id, status="pending")
    client.post(f"/admin/payments/{payment_id}/status", data={"status": "paid"})
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        first_expiry = model.access_expires_at
        assert model.license_type == "academic"
        payment = db.session.get(Payment, payment_id)
        assert payment.invoice_number  # marked paid -> invoiced
    client.post(f"/admin/payments/{payment_id}/status", data={"status": "paid"})
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).access_expires_at == first_expiry


def test_refunding_an_older_payment_keeps_a_newer_paid_license(client):
    _make_admin(client)
    model_id = _seed_model(client, license_type="extended_archive")
    old_id = _seed_payment(client, model_id, plan_key="academic", paid_at=datetime.now(UTC) - timedelta(days=30))
    _seed_payment(client, model_id, plan_key="extended_archive", paid_at=datetime.now(UTC))
    client.post(f"/admin/payments/{old_id}/status", data={"status": "refunded"})
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.license_type == "extended_archive"
        assert model.access_expires_at is None or model.access_expires_at > datetime.now(UTC).replace(tzinfo=None)


def test_paid_back_to_pending_revokes_the_license(client):
    _make_admin(client)
    model_id = _seed_model(client, license_type="academic")
    payment_id = _seed_payment(client, model_id, paid_at=datetime.now(UTC))
    client.post(f"/admin/payments/{payment_id}/status", data={"status": "pending"})
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).license_type == "free"


def test_admin_payment_status_settles_coupon_counters(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        coupon = Coupon(code="SAVE10", percent_off=10, max_redemptions=1, reservation_count=1)
        db.session.add(coupon)
        db.session.commit()
        coupon_id = coupon.id
    payment_id = _seed_payment(client, model_id, status="pending", coupon_id=coupon_id, coupon_reservation_active=True)
    client.post(f"/admin/payments/{payment_id}/status", data={"status": "paid"})
    with client.application.app_context():
        coupon = db.session.get(Coupon, coupon_id)
        assert coupon.reservation_count == 0
        assert coupon.redemption_count == 1


def test_bulk_delete_pending_releases_coupon_reservations(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        coupon = Coupon(code="HOLD", percent_off=10, max_redemptions=1, reservation_count=1)
        db.session.add(coupon)
        db.session.commit()
        coupon_id = coupon.id
    _seed_payment(client, model_id, status="pending", coupon_id=coupon_id, coupon_reservation_active=True)
    client.post("/admin/payments/delete-pending")
    with client.application.app_context():
        assert Payment.query.count() == 0
        assert db.session.get(Coupon, coupon_id).reservation_count == 0


def test_payment_action_returns_to_the_filtered_list(client):
    _make_admin(client)
    model_id = _seed_model(client)
    payment_id = _seed_payment(client, model_id, status="pending")
    response = client.post(
        f"/admin/payments/{payment_id}/status",
        data={"status": "failed", "next": "/admin/revenue?pay_status=pending&page=2"},
    )
    assert response.headers["Location"].endswith("/admin/revenue?pay_status=pending&page=2")
    # Only local admin URLs are honoured.
    response = client.post(
        f"/admin/payments/{payment_id}/status", data={"status": "pending", "next": "https://evil.example/admin"}
    )
    assert response.headers["Location"].endswith("/admin/revenue")


def test_admin_recolour_refuses_layered_models(client):
    _make_admin(client)
    layers = {"layers": [{"name": "Bone", "color": "#ffffff"}, {"name": "Skin", "color": "#ffccaa"}]}
    model_id = _seed_model(client, layer_info=layers, appearance_color="#cccccc")
    client.post(
        f"/admin/models/{model_id}/appearance",
        data={"color": "#ff0000", "color_changed": "1", "finish_changed": "0", "next": "model_detail"},
    )
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).appearance_color == "#cccccc"


def test_processing_override_rejects_worker_states_and_missing_glb(client):
    _make_admin(client)
    model_id = _seed_model(client, processing_status="failed", glb_path="/nonexistent/model.glb")
    client.post(f"/admin/models/{model_id}/processing", data={"processing_status": "queued"})
    client.post(f"/admin/models/{model_id}/processing", data={"processing_status": "ready"})
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).processing_status == "failed"


def test_retry_refused_while_another_job_for_the_model_is_pending(client):
    _make_admin(client)
    model_id = _seed_model(client, processing_status="failed")
    with client.application.app_context():
        failed = ConversionJob(model_id=model_id, job_type="model_upload", status="failed", payload={})
        pending = ConversionJob(model_id=model_id, job_type="model_replace", status="pending", payload={})
        db.session.add_all([failed, pending])
        db.session.commit()
        failed_id = failed.id
    client.post(f"/admin/jobs/{failed_id}/retry")
    with client.application.app_context():
        assert db.session.get(ConversionJob, failed_id).status == "failed"
        assert ConversionJob.query.filter_by(model_id=model_id, status="pending").count() == 1


def test_cancelling_a_queued_replacement_clears_the_processing_banner(client):
    _make_admin(client)
    model_id = _seed_model(client, replacement_status="replacement_processing")
    with client.application.app_context():
        version = ModelVersion(model_id=model_id, version_number=2, status="queued")
        db.session.add(version)
        db.session.flush()
        job = ConversionJob(
            model_id=model_id, job_type="model_replace", status="pending",
            payload={"is_replacement": True, "version_id": version.id},
        )
        db.session.add(job)
        db.session.commit()
        job_id, version_id = job.id, version.id
    client.post(f"/admin/jobs/{job_id}/cancel")
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.replacement_status == "replacement_failed"
        assert model.processing_status == "ready"  # the previous GLB keeps serving
        assert db.session.get(ModelVersion, version_id).status == "failed"


def test_poster_regeneration_goes_through_a_worker_job(client, monkeypatch):
    import app as app_module

    calls = []
    monkeypatch.setattr(app_module, "process_poster_job", lambda app, **kwargs: calls.append(kwargs))
    _make_admin(client)
    model_id = _seed_model(client)
    client.post(f"/admin/models/{model_id}/poster/regenerate")
    with client.application.app_context():
        assert ConversionJob.query.filter_by(model_id=model_id, job_type="poster").count() == 1
    assert calls and calls[0]["model_id"] == model_id


def test_ar_doctor_runs_in_the_worker_not_on_get(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "build_ar_doctor_report", lambda app, model: {"blender_on_path": None})
    _make_admin(client)
    model_id = _seed_model(client)
    response = client.get("/admin/ar-doctor")
    assert response.status_code == 302 and "/admin/system" in response.headers["Location"]
    with client.application.app_context():
        assert ConversionJob.query.filter_by(job_type="ar_doctor").count() == 0
    client.post("/admin/ar-doctor/run")
    with client.application.app_context():
        job = ConversionJob.query.filter_by(job_type="ar_doctor").one()
        assert job.model_id == model_id
        assert job.status == "completed"
        assert job.payload["report"] == {"blender_on_path": None}
    assert "AR doctor" in client.get("/admin/system").get_data(as_text=True)


def test_orphan_count_ignores_files_inside_a_models_folder(client):
    _make_admin(client)
    model_id = _seed_model(client)
    converted = client.application.config["CONVERTED_FOLDER"]
    os.makedirs(os.path.join(converted, model_id, "scenes"), exist_ok=True)
    for name in ("model.glb", "poster.png", os.path.join("scenes", "s1.glb")):
        with open(os.path.join(converted, model_id, name), "wb") as handle:
            handle.write(b"x")
    os.makedirs(os.path.join(converted, "deleted-model"), exist_ok=True)
    with open(os.path.join(converted, "deleted-model", "model.glb"), "wb") as handle:
        handle.write(b"x")
    import app as app_module

    with client.application.app_context():
        assert app_module.count_orphan_model_files(converted, {model_id}) == 1


def test_user_detail_spent_is_per_currency(client):
    _make_admin(client)
    model_id = _seed_model(client)
    _seed_payment(client, model_id, paid_at=datetime.now(UTC))
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        db.session.add(Payment(user_id=model.user_id, amount_kurus=500000, currency="TRY", status="paid"))
        db.session.commit()
        owner_id = model.user_id
    text = client.get(f"/admin/users/{owner_id}").get_data(as_text=True)
    assert "9.90 USD" in text and "5000.00 TRY" in text


def test_role_change_refuses_to_demote_a_configured_admin(client):
    client.application.config["ADMIN_EMAILS"] = ["boss@example.com"]
    _make_admin(client)
    with client.application.app_context():
        boss = create_user(email="boss@example.com", username="Boss")
        boss.is_admin = True
        db.session.commit()
        boss_id = boss.id
    client.post(f"/admin/users/{boss_id}/role", data={"is_admin": "0"})
    with client.application.app_context():
        assert db.session.get(User, boss_id).is_admin is True


def test_admin_email_change_requires_reconfirmation(client):
    _make_admin(client)
    with client.application.app_context():
        member = create_user(email="member@example.com", username="Member")
        member.email_verified_at = datetime.now(UTC)
        db.session.commit()
        member_id = member.id
    client.post(f"/admin/users/{member_id}/email", data={"email": "new@example.com"})
    with client.application.app_context():
        member = db.session.get(User, member_id)
        assert member.email == "new@example.com"
        assert member.email_verified_at is None


def test_undeleting_through_the_update_form_clears_the_deletion_stamp(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        paper_id = db.session.get(Model3D, model_id).paper_id
    client.post(f"/admin/papers/{paper_id}/visibility", data={"visibility": "private", "status": "deleted"})
    client.post(f"/admin/papers/{paper_id}/visibility", data={"visibility": "private", "status": "active"})
    with client.application.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.status == "active"
        assert paper.deleted_at is None


def test_user_audit_trail_includes_admin_actions_on_the_user(client):
    _make_admin(client)
    with client.application.app_context():
        member = create_user(email="member@example.com", username="Member")
        member_id = member.id
    client.post(f"/admin/users/{member_id}/deactivate")
    text = client.get(f"/admin/users/{member_id}").get_data(as_text=True)
    assert "admin_user_deactivated" in text


def test_audit_log_csv_includes_details(client):
    _make_admin(client)
    with client.application.app_context():
        db.session.add(AuditLog(event_type="admin_user_deleted", resource_id="7", details={"email": "gone@example.com"}))
        db.session.commit()
    csv_text = client.get("/admin/logs/export.csv").get_data(as_text=True)
    assert "details" in csv_text.splitlines()[0]
    assert "gone@example.com" in csv_text
