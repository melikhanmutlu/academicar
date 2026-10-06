"""Regression tests for the admin panel phase-0 fixes: pricing/institution
input validation, plan locks, contract restore, coupon double counting,
provider filter, layered appearance saves, mirror retry and the worker stall
threshold."""
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from models import (
    AuditLog,
    ConversionJob,
    Coupon,
    Institution,
    LicensePlanConfig,
    Model3D,
    Paper,
    Payment,
    User,
    db,
)
from tests.conftest import create_user, login
from tests.test_institutions import add_member, create_institution


def _make_admin(client, email="admin@example.com"):
    with client.application.app_context():
        admin = create_user(email=email, username="Admin User")
        admin.is_admin = True
        db.session.commit()
    login(client, email=email)


def _seed_model(client, model_id="p0-model-1", owner_email="owner@example.com", **overrides):
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


def _plan(client, key):
    with client.application.app_context():
        row = LicensePlanConfig.query.filter_by(key=key).one()
        return {
            "price": row.price_usd_cents,
            "duration": row.duration_days,
            "storage": row.storage_limit_bytes,
            "purchasable": row.is_purchasable,
            "models": row.max_models_per_project,
        }


def _post_plan(client, key, **overrides):
    data = {
        "label": key.title(), "price_usd": "9.90", "duration_days": "365",
        "storage_limit_mb": "200", "is_purchasable": "1",
    }
    data.update(overrides)
    return client.post(f"/admin/pricing/{key}", data=data, follow_redirects=True)


# --- 1. unlimited duration must not break public pages ----------------------

def test_public_pricing_survives_unlimited_paid_duration(client):
    _make_admin(client)
    client.get("/admin/pricing")
    _post_plan(client, "academic", duration_days="")
    _post_plan(client, "extended_archive", duration_days="")
    client.post("/auth/logout")
    response = client.get("/pricing")
    assert response.status_code == 200
    assert "Unlimited" in response.get_data(as_text=True)


def test_upgrade_pages_survive_unlimited_paid_duration(client):
    _make_admin(client)
    client.get("/admin/pricing")
    _post_plan(client, "academic", duration_days="")
    client.post("/auth/logout")
    model_id = _seed_model(client, access_expires_at=datetime.now(UTC) - timedelta(days=1))
    login(client, email="owner@example.com")
    assert client.get(f"/models/{model_id}/upgrade").status_code == 200
    assert client.get(f"/view/{model_id}").status_code == 410


# --- 2. numeric validation --------------------------------------------------

@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "1e400", "1e12", "10000.01"])
def test_pricing_rejects_non_finite_or_huge_price(client, bad):
    _make_admin(client)
    client.get("/admin/pricing")
    before = _plan(client, "academic")
    response = _post_plan(client, "academic", price_usd=bad)
    assert response.status_code == 200
    assert _plan(client, "academic") == before


@pytest.mark.parametrize(
    "field,value",
    [("storage_limit_mb", "5000"), ("storage_limit_mb", "9" * 30), ("duration_days", "9" * 30),
     ("duration_days", "40000"), ("max_models_per_project", "9" * 30)],
)
def test_pricing_rejects_out_of_range_integers(client, field, value):
    _make_admin(client)
    client.get("/admin/pricing")
    before = _plan(client, "academic")
    response = _post_plan(client, "academic", **{field: value})
    assert response.status_code == 200
    assert _plan(client, "academic") == before


@pytest.mark.parametrize("bad", ["nan", "inf", "1e400", "1e12", "10000.01"])
def test_coupon_rejects_non_finite_or_huge_fixed_amount(client, bad):
    _make_admin(client)
    response = client.post(
        "/admin/coupons",
        data={"code": "BADFIXED", "discount_type": "fixed", "discount_value": bad},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        assert Coupon.query.count() == 0


def test_coupon_rejects_huge_max_redemptions(client):
    _make_admin(client)
    for bad in ("9" * 30, "²"):
        response = client.post(
            "/admin/coupons",
            data={"code": "BADMAX", "discount_type": "percent", "discount_value": "10", "max_redemptions": bad},
            follow_redirects=True,
        )
        assert response.status_code == 200
    with client.application.app_context():
        assert Coupon.query.count() == 0


def test_coupon_accepts_sane_fixed_amount(client):
    _make_admin(client)
    client.post("/admin/coupons", data={"code": "OKFIXED", "discount_type": "fixed", "discount_value": "5"})
    with client.application.app_context():
        assert Coupon.query.filter_by(code="OKFIXED").one().fixed_discount_usd_cents == 500


# --- 3. Free / Institutional plan locks -------------------------------------

def test_free_plan_price_and_purchasable_are_locked(client):
    _make_admin(client)
    client.get("/admin/pricing")
    before = _plan(client, "free")
    _post_plan(client, "free", price_usd="5", is_purchasable="0", duration_days="7", storage_limit_mb="150")
    after = _plan(client, "free")
    assert after["price"] == before["price"] == 0
    assert after["purchasable"] == before["purchasable"]
    # Free duration is the real free-access window (apply_model_license_defaults).
    assert after["duration"] == 7
    assert after["storage"] == 150 * 1024 * 1024


def test_institutional_plan_price_duration_purchasable_are_locked(client):
    _make_admin(client)
    client.get("/admin/pricing")
    before = _plan(client, "institutional")
    _post_plan(client, "institutional", price_usd="5", duration_days="30", is_purchasable="1", storage_limit_mb="600")
    after = _plan(client, "institutional")
    assert (after["price"], after["duration"], after["purchasable"]) == (
        before["price"], before["duration"], before["purchasable"],
    )
    assert after["storage"] == 600 * 1024 * 1024


def test_locked_plans_hide_price_and_purchasable_inputs(client):
    _make_admin(client)
    html = client.get("/admin/pricing").get_data(as_text=True)
    cards = html.split('<details class="admin-plan-card"')[1:]
    by_key = {}
    for card in cards:
        for key in ("free", "academic", "extended_archive", "institutional"):
            if f"<code>{key}</code>" in card:
                by_key[key] = card
    assert 'name="price_usd"' not in by_key["free"]
    assert 'name="is_purchasable"' not in by_key["free"]
    assert 'name="duration_days"' in by_key["free"]
    for locked in ("institutional",):
        for field in ("price_usd", "is_purchasable", "duration_days"):
            assert f'name="{field}"' not in by_key[locked]
    for field in ("price_usd", "is_purchasable", "duration_days"):
        assert f'name="{field}"' in by_key["academic"]


# --- 4. institution numeric validation --------------------------------------

BAD_INSTITUTION_FIELDS = [
    {"annual_price": "nan"}, {"annual_price": "inf"}, {"annual_price": "1e400"}, {"annual_price": "1e12"},
    {"quota_storage_mb": "nan"}, {"quota_storage_mb": "inf"}, {"quota_storage_mb": "1e400"},
    {"quota_model_count": "²"}, {"quota_model_count": "9" * 30},
    {"currency": "x"}, {"currency": "TR1"},
]


@pytest.mark.parametrize("bad", BAD_INSTITUTION_FIELDS)
def test_institution_create_rejects_bad_numbers_and_currency(client, bad):
    _make_admin(client)
    data = {"name": "Bad Uni", "currency": "TRY", **bad}
    response = client.post("/admin/institutions/create", data=data, follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        assert Institution.query.count() == 0


@pytest.mark.parametrize("bad", BAD_INSTITUTION_FIELDS)
def test_institution_update_rejects_bad_numbers_and_currency(client, bad):
    _make_admin(client)
    with client.application.app_context():
        institution_id = create_institution(name="Keep Uni").id
    data = {"name": "Keep Uni", "currency": "TRY", **bad}
    response = client.post(f"/admin/institutions/{institution_id}/update", data=data, follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        institution = db.session.get(Institution, institution_id)
        assert institution.annual_price_cents is None
        assert institution.quota_storage_bytes is None
        assert institution.quota_model_count is None
        assert institution.currency == "TRY"


@pytest.mark.parametrize(
    "bad", [{"amount": "nan"}, {"amount": "inf"}, {"amount": "1e400"}, {"amount": "1e12"}, {"amount": "10", "currency": "x"}]
)
def test_institution_payment_rejects_bad_amount_and_currency(client, bad):
    _make_admin(client)
    with client.application.app_context():
        institution_id = create_institution(name="Pay Uni").id
    response = client.post(
        f"/admin/institutions/{institution_id}/payments", data={"status": "pending", **bad}, follow_redirects=True
    )
    assert response.status_code == 200
    with client.application.app_context():
        assert Payment.query.count() == 0


# --- 5. end access / reactivate ---------------------------------------------

def _funded_model(client, institution_id, model_id="p0-inst-model"):
    _seed_model(
        client, model_id=model_id, license_type="institutional", institution_id=institution_id,
        license_status="active", access_expires_at=datetime.now(UTC) + timedelta(days=300),
    )
    return model_id


def test_reactivate_after_end_access_restores_funded_models(client):
    _make_admin(client)
    with client.application.app_context():
        institution_id = create_institution(contract_ends_at=datetime.now(UTC) + timedelta(days=300)).id
    model_id = _funded_model(client, institution_id)
    client.post(f"/admin/institutions/{institution_id}/end-access", follow_redirects=True)
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).license_status == "expired"
    response = client.post(
        f"/admin/institutions/{institution_id}/status", data={"status": "active"}, follow_redirects=True
    )
    assert "Access restored on 1 model(s)" in response.get_data(as_text=True)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.license_status == "active"
        institution = db.session.get(Institution, institution_id)
        assert model.access_expires_at == institution.contract_ends_at


def test_reactivate_does_not_restore_models_when_contract_has_ended(client):
    _make_admin(client)
    with client.application.app_context():
        institution_id = create_institution(
            contract_starts_at=datetime.now(UTC) - timedelta(days=400),
            contract_ends_at=datetime.now(UTC) - timedelta(days=5),
        ).id
    model_id = _funded_model(client, institution_id)
    client.post(f"/admin/institutions/{institution_id}/end-access", follow_redirects=True)
    client.post(f"/admin/institutions/{institution_id}/status", data={"status": "active"}, follow_redirects=True)
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).license_status == "expired"


def test_same_end_date_update_after_end_access_is_honest(client):
    _make_admin(client)
    with client.application.app_context():
        institution_id = create_institution(contract_ends_at=datetime(2031, 1, 15, 23, 59, 59)).id
    _funded_model(client, institution_id)
    client.post(f"/admin/institutions/{institution_id}/end-access", follow_redirects=True)
    response = client.post(
        f"/admin/institutions/{institution_id}/update",
        data={"name": "Test University", "contract_ends_at": "2031-01-15", "currency": "TRY"},
        follow_redirects=True,
    )
    text = response.get_data(as_text=True)
    assert "were not restored" in text


def test_past_contract_end_date_warns(client):
    _make_admin(client)
    with client.application.app_context():
        institution_id = create_institution().id
    response = client.post(
        f"/admin/institutions/{institution_id}/update",
        data={"name": "Test University", "contract_ends_at": "2020-01-01", "currency": "TRY"},
        follow_redirects=True,
    )
    assert "in the past" in response.get_data(as_text=True)


# --- 6. coupon redemption counted once per payment --------------------------

def test_coupon_redemption_counts_once_across_status_cycles(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        coupon = Coupon(code="ONCE", percent_off=10, max_redemptions=5, reservation_count=1)
        db.session.add(coupon)
        db.session.commit()
        coupon_id = coupon.id
        model = db.session.get(Model3D, model_id)
        payment = Payment(
            user_id=model.user_id, model_id=model_id, plan_key="academic", amount_kurus=990, currency="USD",
            provider="manual", status="pending", coupon_id=coupon_id, coupon_reservation_active=True,
        )
        db.session.add(payment)
        db.session.commit()
        payment_id = payment.id
    for status in ("paid", "failed", "paid", "pending", "paid"):
        client.post(f"/admin/payments/{payment_id}/status", data={"status": status})
    with client.application.app_context():
        coupon = db.session.get(Coupon, coupon_id)
        assert coupon.redemption_count == 1
        assert coupon.reservation_count == 0


def test_unreserved_payment_first_paid_still_counts_once(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        coupon = Coupon(code="LATE", percent_off=10)
        db.session.add(coupon)
        db.session.commit()
        coupon_id = coupon.id
        model = db.session.get(Model3D, model_id)
        payment = Payment(
            user_id=model.user_id, model_id=model_id, plan_key="academic", amount_kurus=990, currency="USD",
            provider="manual", status="failed", coupon_id=coupon_id,
        )
        db.session.add(payment)
        db.session.commit()
        payment_id = payment.id
    for status in ("paid", "failed", "paid"):
        client.post(f"/admin/payments/{payment_id}/status", data={"status": status})
    with client.application.app_context():
        assert db.session.get(Coupon, coupon_id).redemption_count == 1


# --- 7. provider filter -----------------------------------------------------

def _add_payment(provider, ref):
    db.session.add(Payment(plan_key="academic", amount_kurus=990, currency="USD", provider=provider,
                           provider_reference=ref, status="paid"))


def test_revenue_provider_filter_knows_real_providers(client):
    _make_admin(client)
    with client.application.app_context():
        _add_payment("lemonsqueezy", "ref-lemon")
        _add_payment("development", "ref-dev")
        _add_payment("manual", "ref-manual")
        db.session.commit()
    page = client.get("/admin/revenue?provider=lemonsqueezy").get_data(as_text=True)
    assert 'value="lemonsqueezy"' in page and 'value="development"' in page
    csv = client.get("/admin/revenue/export.csv?provider=lemonsqueezy").get_data(as_text=True)
    assert "ref-lemon" in csv
    assert "ref-dev" not in csv and "ref-manual" not in csv
    csv_all = client.get("/admin/revenue/export.csv").get_data(as_text=True)
    assert "ref-dev" in csv_all and "ref-lemon" in csv_all


def test_revenue_page_provider_filter_applies_to_rows(client):
    _make_admin(client)
    with client.application.app_context():
        _add_payment("lemonsqueezy", "ref-lemon")
        _add_payment("development", "ref-dev")
        db.session.commit()
    page = client.get("/admin/revenue?provider=development").get_data(as_text=True)
    assert "<td>development</td>" in page
    assert "<td>lemonsqueezy</td>" not in page


# --- 8. layered model appearance form ---------------------------------------

LAYER_INFO = {"layers": [{"name": "Bone", "color": "#ffffff"}, {"name": "Skin", "color": "#ffccaa"}]}


@pytest.mark.parametrize("who", ["admin", "owner"])
def test_layered_model_appearance_saves_name_without_colour(client, who):
    _make_admin(client)
    model_id = _seed_model(client, layer_info=LAYER_INFO, appearance_color="#cccccc")
    if who == "owner":
        client.post("/auth/logout")
        login(client, email="owner@example.com")
        url = f"/models/{model_id}/appearance"
    else:
        url = f"/admin/models/{model_id}/appearance"
    response = client.post(
        url,
        data={"color_changed": "0", "finish_changed": "0", "display_name": "Named", "description": "Desc",
              "ar_placement": "wall"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Provide a valid hex color" not in response.get_data(as_text=True)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.display_name == "Named"
        assert model.description == "Desc"
        assert model.ar_placement == "wall"
        assert model.appearance_color == "#cccccc"


def test_changed_colour_still_requires_valid_hex(client):
    _make_admin(client)
    model_id = _seed_model(client, appearance_color="#cccccc")
    client.post(
        f"/admin/models/{model_id}/appearance",
        data={"color_changed": "1", "color": "nonsense", "display_name": "X"},
    )
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).display_name is None


# --- 9. mirror retry ---------------------------------------------------------

def _flagged_model(client):
    model_id = _seed_model(client, r2_mirror_failed_at=datetime.now(UTC))
    os.makedirs(os.path.join(client.application.config["CONVERTED_FOLDER"], model_id), exist_ok=True)
    return model_id


def test_mirror_retry_refuses_when_r2_disabled(client):
    _make_admin(client)
    model_id = _flagged_model(client)
    with patch("app.r2_mirror_enabled", return_value=False), patch("app.mirror_directory_sync") as sync:
        response = client.post(f"/admin/models/{model_id}/mirror/retry", follow_redirects=True)
    assert "not enabled" in response.get_data(as_text=True)
    assert not sync.called
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).r2_mirror_failed_at is not None
        assert AuditLog.query.filter_by(event_type="admin_model_mirror_retried").count() == 0


def test_mirror_retry_clears_flag_only_on_real_success(client):
    _make_admin(client)
    model_id = _flagged_model(client)
    with patch("app.r2_mirror_enabled", return_value=True), patch("app.mirror_directory_sync", return_value=True):
        client.post(f"/admin/models/{model_id}/mirror/retry", follow_redirects=True)
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).r2_mirror_failed_at is None


def test_mirror_retry_with_missing_local_files_keeps_flag(client):
    _make_admin(client)
    model_id = _seed_model(client, r2_mirror_failed_at=datetime.now(UTC))
    with patch("app.r2_mirror_enabled", return_value=True), patch("app.mirror_directory_sync", return_value=True):
        client.post(f"/admin/models/{model_id}/mirror/retry", follow_redirects=True)
    with client.application.app_context():
        assert db.session.get(Model3D, model_id).r2_mirror_failed_at is not None


# --- 10. WORKER_STALL_MINUTES ------------------------------------------------

@pytest.mark.parametrize("bad", ["abc", "", "-5", "0", "1e3"])
def test_invalid_worker_stall_minutes_does_not_500(client, monkeypatch, bad):
    monkeypatch.setenv("WORKER_STALL_MINUTES", bad)
    _make_admin(client)
    assert client.get("/health/worker").status_code == 200
    assert client.get("/admin/system").status_code == 200


def test_invalid_worker_stall_minutes_falls_back_to_thirty(client, monkeypatch):
    monkeypatch.setenv("WORKER_STALL_MINUTES", "nonsense")
    with client.application.app_context():
        job = ConversionJob(model_id="x", status="pending")
        db.session.add(job)
        db.session.commit()
        job.created_at = datetime.now(UTC) - timedelta(minutes=40)
        db.session.commit()
    assert client.get("/health/worker").status_code == 503
