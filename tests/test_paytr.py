"""Tests for the PayTR gateway callback path."""
import base64
import hashlib
import hmac
import uuid

import pytest

from app import create_app, limiter
from models import Model3D, Paper, Payment, User, db

MERCHANT_KEY = "testmerchantkey"
MERCHANT_SALT = "testmerchantsalt"


@pytest.fixture()
def paytr_app(tmp_path):
    flask_app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'paytr.db'}",
            "SECRET_KEY": "test-secret",
            "PAYMENT_PROVIDER": "paytr",
            "PAYTR_MERCHANT_ID": "12345",
            "PAYTR_MERCHANT_KEY": MERCHANT_KEY,
            "PAYTR_MERCHANT_SALT": MERCHANT_SALT,
            "UPLOAD_FOLDER": str(tmp_path / "u"),
            "CONVERTED_FOLDER": str(tmp_path / "c"),
            "QR_FOLDER": str(tmp_path / "q"),
            "PDF_FOLDER": str(tmp_path / "p"),
        }
    )
    limiter.reset()
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
    yield flask_app
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def paytr_client(paytr_app):
    return paytr_app.test_client()


def _make_pending_payment(app, plan_key="academic"):
    """Create an owned model + the pending Payment a PayTR checkout would have
    written, returning (model_id, merchant_oid)."""
    with app.app_context():
        user = User(email="p@example.com", username="P")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(title="T", slug=f"paytr-{uuid.uuid4().hex[:6]}", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id=uuid.uuid4().hex,
            paper_id=paper.id,
            user_id=user.id,
            glb_path="c/m.glb",
            license_type="free",
            processing_status="ready",
        )
        db.session.add(model)
        merchant_oid = "AAR" + uuid.uuid4().hex[:10]
        payment = Payment(
            user_id=user.id,
            paper_id=paper.id,
            model_id=model.id,
            plan_key=plan_key,
            amount_kurus=990,
            currency="USD",
            provider="paytr",
            provider_reference=merchant_oid,
            status="pending",
        )
        db.session.add(payment)
        db.session.commit()
        return model.id, merchant_oid


def _paytr_hash(merchant_oid, status, total_amount):
    return base64.b64encode(
        hmac.new(
            MERCHANT_KEY.encode(),
            (merchant_oid + MERCHANT_SALT + status + total_amount).encode(),
            hashlib.sha256,
        ).digest()
    ).decode()


def test_paytr_callback_applies_license_and_acks_ok(paytr_client, paytr_app):
    model_id, merchant_oid = _make_pending_payment(paytr_app)
    total_amount = "990"
    data = {
        "merchant_oid": merchant_oid,
        "status": "success",
        "total_amount": total_amount,
        "hash": _paytr_hash(merchant_oid, "success", total_amount),
    }
    resp = paytr_client.post("/payment/webhook/paytr", data=data)
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "OK"
    with paytr_app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "academic"
        payment = Payment.query.filter_by(provider_reference=merchant_oid).first()
        assert payment.status == "paid"


def test_paytr_callback_rejects_underpayment(paytr_client, paytr_app):
    """A signed 'success' callback paying less than the stored amount must not
    grant the license (the signature proves authenticity, not the amount)."""
    model_id, merchant_oid = _make_pending_payment(paytr_app)  # amount_kurus=990
    total_amount = "1"  # 1 kuruş instead of 990
    data = {
        "merchant_oid": merchant_oid,
        "status": "success",
        "total_amount": total_amount,
        "hash": _paytr_hash(merchant_oid, "success", total_amount),
    }
    resp = paytr_client.post("/payment/webhook/paytr", data=data)
    assert resp.status_code == 400
    with paytr_app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "free"
        assert Payment.query.filter_by(provider_reference=merchant_oid).first().status == "pending"


def test_paytr_callback_rejects_bad_hash(paytr_client, paytr_app):
    model_id, merchant_oid = _make_pending_payment(paytr_app)
    resp = paytr_client.post(
        "/payment/webhook/paytr",
        data={"merchant_oid": merchant_oid, "status": "success", "total_amount": "990", "hash": "wrong"},
    )
    assert resp.status_code == 400
    with paytr_app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "free"


def test_paytr_callback_is_idempotent(paytr_client, paytr_app):
    model_id, merchant_oid = _make_pending_payment(paytr_app)
    data = {
        "merchant_oid": merchant_oid,
        "status": "success",
        "total_amount": "990",
        "hash": _paytr_hash(merchant_oid, "success", "990"),
    }
    assert paytr_client.post("/payment/webhook/paytr", data=data).get_data(as_text=True) == "OK"
    assert paytr_client.post("/payment/webhook/paytr", data=data).get_data(as_text=True) == "OK"
    with paytr_app.app_context():
        paid = Payment.query.filter_by(provider_reference=merchant_oid, status="paid").all()
        assert len(paid) == 1


def test_paytr_callback_failed_status_does_not_grant(paytr_client, paytr_app):
    model_id, merchant_oid = _make_pending_payment(paytr_app)
    data = {
        "merchant_oid": merchant_oid,
        "status": "failed",
        "total_amount": "0",
        "hash": _paytr_hash(merchant_oid, "failed", "0"),
    }
    resp = paytr_client.post("/payment/webhook/paytr", data=data)
    assert resp.get_data(as_text=True) == "OK"  # acknowledge so PayTR stops retrying
    with paytr_app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "free"
