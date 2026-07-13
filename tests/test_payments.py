"""Tests for the provider-agnostic payment skeleton.

Covers the checkout -> license-assignment path (dev provider settles instantly),
ownership enforcement, rejection of non-buyable plans, and idempotent webhook
processing.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import payments
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


def test_upgrade_redirects_to_publication_with_success_marker(client, app):
    """The buyer must land back on the publication page (not the viewer, which
    can 410 while the async webhook is still in flight) with the ?upgraded marker
    that drives the success banner."""
    from tests.conftest import login, register

    register(client)
    login(client)
    model_id = _make_model(app)

    resp = client.post(f"/models/{model_id}/upgrade/academic", follow_redirects=False)
    assert resp.status_code in (302, 303)
    location = resp.headers["Location"]
    assert "/papers/" in location
    assert f"upgraded={model_id}" in location


def test_publication_shows_success_banner_after_upgrade(client, app):
    from tests.conftest import login, register

    register(client)
    login(client)
    model_id = _make_model(app)
    # Dev provider settles instantly, so the model is active on landing.
    client.post(f"/models/{model_id}/upgrade/academic", follow_redirects=False)
    with app.app_context():
        slug = db.session.get(Model3D, model_id).paper.slug

    body = client.get(f"/papers/{slug}?upgraded={model_id}").get_data(as_text=True)
    assert "Payment successful" in body


def test_publication_no_banner_without_marker(client, app):
    from tests.conftest import login, register

    register(client)
    login(client)
    model_id = _make_model(app)
    with app.app_context():
        slug = db.session.get(Model3D, model_id).paper.slug

    body = client.get(f"/papers/{slug}").get_data(as_text=True)
    assert "Payment successful" not in body
    assert "Payment received" not in body


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


def test_webhook_rejects_event_model_id_mismatch(client, app):
    """A webhook resolved to a stored Payment must not license a different model
    than the one the Payment is bound to (IDOR guard)."""
    from tests.conftest import login, register

    register(client)
    login(client)
    model_a = _make_model(app)
    model_b = _make_model(app)
    with app.app_context():
        a = db.session.get(Model3D, model_a)
        pay = Payment(
            user_id=a.user_id, paper_id=a.paper_id, model_id=a.id,
            plan_key="academic", amount_kurus=990, currency="USD",
            provider="development", provider_reference="dev-mismatch-1", status="pending",
        )
        db.session.add(pay)
        db.session.commit()

    # Event names model_b while the Payment is bound to model_a.
    resp = client.post(
        "/payment/webhook/development",
        json={"provider_reference": "dev-mismatch-1", "status": "paid",
              "plan_key": "academic", "model_id": model_b},
    )
    assert resp.status_code == 400
    with app.app_context():
        assert db.session.get(Model3D, model_a).license_type == "free"
        assert db.session.get(Model3D, model_b).license_type == "free"
        assert Payment.query.filter_by(provider_reference="dev-mismatch-1").first().status == "pending"


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


# --- TCMB USD/TRY conversion (PayTR settles in TRY) -------------------------

_TCMB_XML = b"""<Tarih_Date Tarih="13.07.2026" Date="07/13/2026" Bulten_No="2026/133">
<Currency CrossOrder="0" Kod="USD" CurrencyName="US DOLLAR">
<Unit>1</Unit>
<Isim>ABD DOLARI</Isim>
<CurrencyName>US DOLLAR</CurrencyName>
<ForexBuying>34.0000</ForexBuying>
<ForexSelling>34.1000</ForexSelling>
<BanknoteBuying>33.9000</BanknoteBuying>
<BanknoteSelling>34.2000</BanknoteSelling>
</Currency>
</Tarih_Date>"""


class _FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _reset_fx_cache(monkeypatch):
    monkeypatch.setattr(payments, "_fx_cache", {"rate": None, "fetched_at": None})


def test_forex_rate_parses_tcmb_forex_selling(monkeypatch):
    _reset_fx_cache(monkeypatch)
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(_TCMB_XML))
    assert payments.get_usd_try_forex_selling_rate() == pytest.approx(34.10)


def test_forex_rate_is_cached_within_ttl(monkeypatch):
    _reset_fx_cache(monkeypatch)
    calls = []
    monkeypatch.setattr("requests.get", lambda *a, **k: calls.append(1) or _FakeResponse(_TCMB_XML))
    payments.get_usd_try_forex_selling_rate()
    payments.get_usd_try_forex_selling_rate()
    assert len(calls) == 1


def test_forex_rate_falls_back_to_stale_cache_on_error(monkeypatch):
    _reset_fx_cache(monkeypatch)
    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResponse(_TCMB_XML))
    first = payments.get_usd_try_forex_selling_rate()

    # Expire the cache, then make the refetch fail — must fall back, not raise.
    payments._fx_cache["fetched_at"] = datetime.now(UTC) - timedelta(hours=2)

    def failing_get(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("requests.get", failing_get)
    assert payments.get_usd_try_forex_selling_rate() == first


def test_forex_rate_raises_when_never_cached_and_fetch_fails(monkeypatch):
    _reset_fx_cache(monkeypatch)

    def failing_get(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("requests.get", failing_get)
    with pytest.raises(payments.ForexRateUnavailable):
        payments.get_usd_try_forex_selling_rate()


def test_plan_amount_minor_units_usd_unchanged():
    assert payments.plan_amount_minor_units("academic", "USD") == 990
    assert payments.plan_amount_minor_units("academic") == 990  # default currency


def test_plan_amount_minor_units_try_uses_tcmb_rate(monkeypatch):
    monkeypatch.setattr(payments, "get_usd_try_forex_selling_rate", lambda: 34.10)
    # $9.90 * 34.10 TRY/USD = 337.59 TRY -> 33759 kurus
    assert payments.plan_amount_minor_units("academic", "TRY") == 33759
    assert payments.plan_amount_minor_units("academic", "TL") == 33759


def test_upgrade_uses_try_conversion_when_configured(client, app, monkeypatch):
    from tests.conftest import login, register

    app.config["PAYMENT_CURRENCY"] = "TRY"
    monkeypatch.setattr(payments, "get_usd_try_forex_selling_rate", lambda: 34.10)

    register(client)
    login(client)
    model_id = _make_model(app)

    resp = client.post(f"/models/{model_id}/upgrade/academic", follow_redirects=False)
    assert resp.status_code in (302, 303)

    with app.app_context():
        payment = Payment.query.filter_by(model_id=model_id, status="paid").first()
        assert payment is not None
        assert payment.currency == "TRY"
        assert payment.amount_kurus == 33759


def test_upgrade_shows_friendly_error_when_fx_rate_unavailable(client, app, monkeypatch):
    from tests.conftest import login, register

    app.config["PAYMENT_CURRENCY"] = "TRY"
    _reset_fx_cache(monkeypatch)

    def failing_get(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("requests.get", failing_get)

    register(client)
    login(client)
    model_id = _make_model(app)

    resp = client.post(f"/models/{model_id}/upgrade/academic", follow_redirects=True)
    assert resp.status_code == 200
    assert b"exchange rate" in resp.data
    with app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "free"
