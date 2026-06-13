"""Regression tests for the v12 review fixes (QR disable, admin payment license)."""
import uuid

from licensing import apply_model_license_defaults, is_access_expired
from models import Model3D, Paper, Payment, QRLink, User, db


def _public_model(app, *, license="academic", qr_status="active"):
    with app.app_context():
        u = User(email=f"v12-{uuid.uuid4().hex[:6]}@e.com", username="V")
        u.set_password("password123")
        db.session.add(u)
        db.session.flush()
        p = Paper(title="T", slug=f"v12-{uuid.uuid4().hex[:6]}", user_id=u.id, is_public=True)
        db.session.add(p)
        db.session.flush()
        m = Model3D(
            id=uuid.uuid4().hex, paper_id=p.id, user_id=u.id, glb_path="c/m.glb",
            processing_status="ready", public_id=uuid.uuid4().hex[:16],
        )
        apply_model_license_defaults(m, license)
        db.session.add(m)
        db.session.flush()
        db.session.add(QRLink(public_id=m.public_id, model_id=m.id, status=qr_status, target_type="model_viewer"))
        db.session.commit()
        return m.public_id, m.id


def test_disabled_qr_link_does_not_resolve(client):
    pub, _ = _public_model(client.application, qr_status="disabled")
    assert client.get(f"/m/{pub}", follow_redirects=False).status_code == 404


def test_active_qr_link_resolves_to_viewer(client):
    pub, mid = _public_model(client.application, qr_status="active")
    r = client.get(f"/m/{pub}", follow_redirects=False)
    assert r.status_code == 302
    assert f"/view/{mid}" in r.headers["Location"]


def test_admin_paid_grants_and_refund_revokes_license(client):
    from tests.conftest import login

    _, mid = _public_model(client.application, license="free")
    with client.application.app_context():
        admin = User(email="v12admin@e.com", username="A", is_admin=True)
        admin.set_password("password123")
        db.session.add(admin)
        m = db.session.get(Model3D, mid)
        pay = Payment(
            user_id=m.user_id, paper_id=m.paper_id, model_id=m.id, plan_key="academic",
            amount_kurus=990, currency="USD", provider="paytr", provider_reference="adm-1", status="pending",
        )
        db.session.add(pay)
        db.session.commit()
        pay_id = pay.id

    login(client, email="v12admin@e.com", password="password123")

    # Marking paid grants the licensed window.
    client.post(f"/admin/payments/{pay_id}/status", data={"status": "paid"})
    with client.application.app_context():
        m = db.session.get(Model3D, mid)
        assert m.license_type == "academic"
        assert not is_access_expired(m.access_expires_at)

    # Refunding revokes access immediately.
    client.post(f"/admin/payments/{pay_id}/status", data={"status": "refunded"})
    with client.application.app_context():
        m = db.session.get(Model3D, mid)
        assert is_access_expired(m.access_expires_at)
