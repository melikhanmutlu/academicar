"""Tests for the admin pricing panel (Faz P): editing, validation, and the
audit trail. See tests/test_license_plan_pricing.py for the cache/checkout/
webhook behavior tests."""
from models import AuditLog, LicensePlanConfig, db
from tests.conftest import create_user, login


def _make_admin(client, email="admin@example.com"):
    with client.application.app_context():
        admin = create_user(email=email, username="Admin User")
        admin.is_admin = True
        db.session.commit()
    login(client, email=email)


def test_pricing_page_self_heals_rows_for_admin(client):
    _make_admin(client)
    response = client.get("/admin/pricing")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Academic" in text
    assert "Extended Archive" in text
    with client.application.app_context():
        assert LicensePlanConfig.query.count() == 4


def test_pricing_page_forbidden_for_member(client):
    with client.application.app_context():
        create_user()
    login(client)
    assert client.get("/admin/pricing").status_code == 403
    assert client.post("/admin/pricing/academic", data={}).status_code == 403


def test_admin_can_update_pricing(client):
    _make_admin(client)
    client.get("/admin/pricing")  # trigger seed
    response = client.post(
        "/admin/pricing/academic",
        data={"label": "Academic", "price_usd": "14.90", "duration_days": "730", "storage_limit_mb": "300", "is_purchasable": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        row = LicensePlanConfig.query.filter_by(key="academic").one()
        assert row.price_usd_cents == 1490
        assert row.duration_days == 730
        assert row.storage_limit_bytes == 300 * 1024 * 1024
        assert AuditLog.query.filter_by(event_type="admin_pricing_changed").count() == 1


def test_pricing_update_rejects_negative_price(client):
    _make_admin(client)
    client.get("/admin/pricing")
    client.post(
        "/admin/pricing/academic",
        data={"label": "Academic", "price_usd": "-5", "duration_days": "1095", "storage_limit_mb": "200", "is_purchasable": "1"},
        follow_redirects=True,
    )
    with client.application.app_context():
        row = LicensePlanConfig.query.filter_by(key="academic").one()
        assert row.price_usd_cents == 990  # unchanged default
        assert AuditLog.query.filter_by(event_type="admin_pricing_changed").count() == 0


def test_pricing_update_rejects_non_positive_duration_and_storage(client):
    _make_admin(client)
    client.get("/admin/pricing")
    client.post(
        "/admin/pricing/academic",
        data={"label": "Academic", "price_usd": "9.90", "duration_days": "0", "storage_limit_mb": "200", "is_purchasable": "1"},
        follow_redirects=True,
    )
    client.post(
        "/admin/pricing/academic",
        data={"label": "Academic", "price_usd": "9.90", "duration_days": "1095", "storage_limit_mb": "-1", "is_purchasable": "1"},
        follow_redirects=True,
    )
    with client.application.app_context():
        row = LicensePlanConfig.query.filter_by(key="academic").one()
        assert row.duration_days == 1095
        assert row.storage_limit_bytes == 200 * 1024 * 1024
        assert AuditLog.query.filter_by(event_type="admin_pricing_changed").count() == 0
