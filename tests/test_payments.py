"""Tests for the provider-agnostic payment skeleton.

Covers the checkout -> license-assignment path (dev provider settles instantly),
ownership enforcement, rejection of non-buyable plans, and idempotent webhook
processing.
"""
import uuid

from models import Model3D, Paper, Payment, User, db


def _make_model(app, user_email="user@example.com", license_type="free"):
    with app.app_context():
        user = User.query.filter_by(email=user_email).first()
        paper = Paper(
            title="Test Paper",
            slug=f"test-{uuid.uuid4().hex[:8]}",
            user_id=user.id,
            is_public=True,
        )
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id=uuid.uuid4().hex,
            paper_id=paper.id,
            user_id=user.id,
            glb_path="converted/test/model.glb",
            license_type=license_type,
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()
        return model.id


def test_paid_upgrade_assigns_license_dev_provider(client, app):
    from tests.conftest import login, register

    register(client)
    login(client)
    model_id = _make_model(app)

    resp = client.post(f"/models/{model_id}/upgrade/academic", follow_redirects=False)
    assert resp.status_code in (302, 303)

    with app.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.license_type == "academic"
        assert model.access_expires_at is not None
        payment = Payment.query.filter_by(model_id=model_id, status="paid").first()
        assert payment is not None
        assert payment.invoice_number
        assert payment.currency == "USD"


def test_upgrade_rejects_non_buyable_plan(client, app):
    from tests.conftest import login, register

    register(client)
    login(client)
    model_id = _make_model(app)

    # "institutional" is provisioned manually, never via self-serve checkout.
    resp = client.post(f"/models/{model_id}/upgrade/institutional", follow_redirects=False)
    assert resp.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "free"


def test_upgrade_requires_ownership(client, app):
    from tests.conftest import create_user, login

    with app.app_context():
        create_user(email="owner@example.com")
        create_user(email="intruder@example.com", username="Intruder")
    model_id = _make_model(app, user_email="owner@example.com")

    login(client, email="intruder@example.com")
    resp = client.post(f"/models/{model_id}/upgrade/academic")
    assert resp.status_code == 403
    with app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "free"


def test_webhook_assigns_license_and_is_idempotent(client, app):
    from tests.conftest import login, register

    register(client)
    login(client)
    model_id = _make_model(app)

    payload = {
        "provider_reference": "dev-order-123",
        "status": "paid",
        "plan_key": "academic",
        "model_id": model_id,
    }
    r1 = client.post("/payment/webhook/development", json=payload)
    assert r1.status_code == 200
    with app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "academic"
        paid = Payment.query.filter_by(provider_reference="dev-order-123", status="paid").all()
        assert len(paid) == 1

    # A duplicate delivery of the same order must not double-process.
    r2 = client.post("/payment/webhook/development", json=payload)
    assert r2.status_code == 200
    assert r2.get_json().get("duplicate") is True
    with app.app_context():
        paid = Payment.query.filter_by(provider_reference="dev-order-123", status="paid").all()
        assert len(paid) == 1


def test_webhook_ignores_unpaid_event(client, app):
    from tests.conftest import login, register

    register(client)
    login(client)
    model_id = _make_model(app)

    resp = client.post(
        "/payment/webhook/development",
        json={"provider_reference": "dev-pending", "status": "pending", "plan_key": "academic", "model_id": model_id},
    )
    assert resp.status_code == 200
    assert resp.get_json().get("ignored") is True
    with app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "free"


def test_webhook_unknown_provider_404(client):
    resp = client.post("/payment/webhook/stripe", json={"status": "paid"})
    assert resp.status_code == 404


def test_unknown_provider_name_does_not_fall_back_to_dev_in_prod():
    """A typo'd PAYMENT_PROVIDER must resolve to a secure provider, never the dev
    provider whose webhook verification accepts forged 'paid' events."""
    from app import create_app
    from payments import get_payment_provider

    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "SECRET_KEY": "x",
            "PAYMENT_PROVIDER": "lemonsqeezy",  # deliberate typo (unrecognized)
            "ALLOW_DEV_PAYMENTS": False,
        }
    )
    with app.app_context():
        assert get_payment_provider().name == "lemonsqueezy"

    # The forged dev webhook path must 404 (provider no longer resolves to dev).
    resp = app.test_client().post(
        "/payment/webhook/development",
        json={"status": "paid", "plan_key": "extended_archive", "model_id": "x"},
    )
    assert resp.status_code == 404
