"""Tests for the license-plan pricing cache and its effect on checkout/webhook
behavior (Faz P). See tests/test_admin_pricing.py for the admin edit-form tests."""
import uuid

from models import LicensePlanConfig, Model3D, Paper, Payment, User, db
from tests.conftest import create_user, login
from tests.test_paytr import MERCHANT_KEY, MERCHANT_SALT, _make_pending_payment, _paytr_hash, paytr_app, paytr_client  # noqa: F401


def _make_admin(client, email="admin@example.com"):
    with client.application.app_context():
        admin = create_user(email=email, username="Admin User")
        admin.is_admin = True
        db.session.commit()
    login(client, email=email)


def _make_model(app, license_type="free"):
    with app.app_context():
        user = User.query.filter_by(email="user@example.com").first()
        paper = Paper(title="Test Paper", slug=f"test-{uuid.uuid4().hex[:8]}", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id=uuid.uuid4().hex, paper_id=paper.id, user_id=user.id,
            glb_path="converted/test/model.glb", license_type=license_type, processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()
        return model.id


def test_price_change_does_not_affect_existing_grant(client, app):
    from tests.conftest import login as base_login, register

    register(client)
    base_login(client)
    model_id = _make_model(app)
    client.post(f"/models/{model_id}/upgrade/academic", follow_redirects=False)
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        expires_before = model.access_expires_at
        payment = Payment.query.filter_by(model_id=model_id, status="paid").first()
        amount_before = payment.amount_kurus

        row = LicensePlanConfig.query.filter_by(key="academic").first()
        if row is None:
            from licensing import default_license_plans
            plan = default_license_plans()["academic"]
            row = LicensePlanConfig(key="academic", label=plan.label, price_usd_cents=990, duration_days=plan.duration_days, storage_limit_bytes=plan.storage_limit_bytes)
            db.session.add(row)
        row.price_usd_cents = 1490
        row.duration_days = 30
        db.session.commit()
        from licensing import refresh_license_plan_cache
        refresh_license_plan_cache()

        model = db.session.get(Model3D, model_id)
        payment = db.session.get(Payment, payment.id)
        assert model.access_expires_at == expires_before
        assert payment.amount_kurus == amount_before


def test_new_checkout_uses_live_price(client, app):
    from tests.conftest import login as base_login, register

    register(client)
    base_login(client)
    model_id = _make_model(app)
    with app.app_context():
        from licensing import default_license_plans, refresh_license_plan_cache
        plan = default_license_plans()["academic"]
        row = LicensePlanConfig(key="academic", label=plan.label, price_usd_cents=1490, duration_days=plan.duration_days, storage_limit_bytes=plan.storage_limit_bytes)
        db.session.add(row)
        db.session.commit()
        refresh_license_plan_cache()

    client.post(f"/models/{model_id}/upgrade/academic", follow_redirects=False)
    with app.app_context():
        payment = Payment.query.filter_by(model_id=model_id, status="paid").first()
        assert payment.amount_kurus == 1490


def test_webhook_amount_check_pinned_to_stored_payment_amount(paytr_client, paytr_app):
    """A price change after checkout must never retroactively affect the
    amount a pending webhook is judged against."""
    model_id, merchant_oid = _make_pending_payment(paytr_app)  # amount_kurus=990
    with paytr_app.app_context():
        from licensing import default_license_plans, refresh_license_plan_cache
        plan = default_license_plans()["academic"]
        row = LicensePlanConfig(key="academic", label=plan.label, price_usd_cents=5000, duration_days=plan.duration_days, storage_limit_bytes=plan.storage_limit_bytes)
        db.session.add(row)
        db.session.commit()
        refresh_license_plan_cache()

    # The original checkout amount (990) is still accepted even though the
    # live price is now 5000 -- the webhook check reads Payment.amount_kurus.
    total_amount = "990"
    data = {
        "merchant_oid": merchant_oid,
        "status": "success",
        "total_amount": total_amount,
        "hash": _paytr_hash(merchant_oid, "success", total_amount),
    }
    resp = paytr_client.post("/payment/webhook/paytr", data=data)
    assert resp.status_code == 200
    with paytr_app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "academic"


def test_cache_reflects_db_change_in_same_process(app):
    with app.app_context():
        from licensing import default_license_plans, get_license_plan, refresh_license_plan_cache
        plan = default_license_plans()["academic"]
        row = LicensePlanConfig(key="academic", label=plan.label, price_usd_cents=1234, duration_days=plan.duration_days, storage_limit_bytes=plan.storage_limit_bytes)
        db.session.add(row)
        db.session.commit()
        refresh_license_plan_cache()
        assert get_license_plan("academic").price_usd == 12.34


def test_cache_falls_back_to_defaults_when_table_empty(app):
    with app.app_context():
        from licensing import get_license_plan, refresh_license_plan_cache
        assert LicensePlanConfig.query.count() == 0
        refresh_license_plan_cache()
        assert get_license_plan("academic").price_usd == 9.90


def test_seed_license_plans_is_idempotent_and_preserves_edits(app):
    with app.app_context():
        from app import seed_license_plans
        seed_license_plans(app)
        assert LicensePlanConfig.query.count() == 4
        row = LicensePlanConfig.query.filter_by(key="academic").one()
        row.price_usd_cents = 7777
        db.session.commit()

        seed_license_plans(app)  # second call must not touch the existing row
        assert LicensePlanConfig.query.count() == 4
        assert LicensePlanConfig.query.filter_by(key="academic").one().price_usd_cents == 7777


def test_seed_license_plans_self_heals_missing_key(app):
    with app.app_context():
        from app import seed_license_plans
        seed_license_plans(app)
        LicensePlanConfig.query.filter_by(key="institutional").delete()
        db.session.commit()
        assert LicensePlanConfig.query.count() == 3

        seed_license_plans(app)
        assert LicensePlanConfig.query.count() == 4
        assert LicensePlanConfig.query.filter_by(key="institutional").first() is not None
