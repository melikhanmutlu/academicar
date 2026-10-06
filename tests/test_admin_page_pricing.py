from models import LicensePlanConfig, db
from tests.conftest import create_user, login


def _admin(client):
    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin User")
        admin.is_admin = True
        db.session.commit()
    login(client, email="admin@example.com")


def test_pricing_page_copy_and_plurals(client):
    _admin(client)
    text = client.get("/admin/pricing").get_data(as_text=True)
    assert "Edit pricing, quotas" not in text
    assert "PayTR" not in text
    assert "4 plans" in text
    assert "0 coupons" in text
    assert "Duration (days)" in text
    assert "Max uses" in text
    assert 'style="margin-top' not in text
    client.post("/admin/coupons", data={"code": "ONE10", "discount_type": "percent", "discount_value": "10"})
    assert "1 coupon<" in client.get("/admin/pricing").get_data(as_text=True)


def test_pricing_visit_does_not_reseed_when_complete(client, monkeypatch):
    _admin(client)
    client.get("/admin/pricing")
    import app as app_module

    calls = []
    monkeypatch.setattr(app_module, "seed_license_plans", lambda a: calls.append(1))
    assert client.get("/admin/pricing").status_code == 200
    assert calls == []
    with client.application.app_context():
        LicensePlanConfig.query.filter_by(key="academic").delete()
        db.session.commit()
    client.get("/admin/pricing")
    assert calls == [1]


def test_feature_summary_has_no_hardcoded_duration_or_model_count():
    from licensing import default_license_plans

    for plan in default_license_plans().values():
        for line in plan.feature_summary:
            assert "year AR" not in line and "day AR" not in line and "interactive model" not in line
