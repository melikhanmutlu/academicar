"""Regression tests for the 2026-10-02 UX/interaction audit fixes."""
import re
import uuid

from models import AuditLog, Coupon, Model3D, Paper, User, db
from tests.conftest import login, register


def _make_model(app, user_email="user@example.com", license_type="free"):
    with app.app_context():
        user = User.query.filter_by(email=user_email).first()
        paper = Paper(title="UX Paper", slug=f"ux-{uuid.uuid4().hex[:8]}", user_id=user.id, is_public=True)
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


# --- Payments -----------------------------------------------------------------

def test_dev_webhook_rejected_outside_test_mode(client, app):
    """An unsigned development webhook must not grant a paid license on a
    running (non-test) deployment that has dev payments enabled."""
    register(client)
    model_id = _make_model(app)
    app.config["TESTING"] = False
    try:
        resp = client.post(
            "/payment/webhook/development",
            json={"status": "paid", "plan_key": "extended_archive", "model_id": model_id},
        )
    finally:
        app.config["TESTING"] = True
    assert resp.status_code == 400
    with app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "free"


# --- Registration ----------------------------------------------------------------

def _register_form(email, **extra):
    data = {"username": "Terms Probe", "email": email, "password": "password123", "confirm": "password123"}
    data.update(extra)
    return data


def test_register_requires_terms_on_server(client, app):
    resp = client.post("/auth/register", data=_register_form("noterms@example.com"))
    assert resp.status_code == 200
    assert b"accept the Terms" in resp.data
    with app.app_context():
        assert User.query.filter_by(email="noterms@example.com").first() is None


def test_register_records_terms_acceptance(client, app):
    resp = client.post("/auth/register", data=_register_form("terms@example.com", accept_terms="y"))
    assert resp.status_code in (302, 303)
    with app.app_context():
        user = User.query.filter_by(email="terms@example.com").first()
        entry = AuditLog.query.filter_by(event_type="user_registered", user_id=user.id).first()
        assert entry is not None
        assert entry.details.get("terms_accepted") is True
        assert entry.details.get("terms_accepted_at")


def test_register_keeps_terms_checkbox_after_error(client):
    data = _register_form("keep@example.com", accept_terms="y")
    data["confirm"] = "different123"
    resp = client.post("/auth/register", data=data)
    assert resp.status_code == 200
    assert re.search(rb'name="accept_terms"[^>]*checked', resp.data)


def test_register_with_plan_reminds_user_of_choice(client):
    resp = client.post(
        "/auth/register?plan=extended_archive",
        data=_register_form("plan@example.com", accept_terms="y"),
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Extended Archive" in resp.data
    assert b"Upgrade" in resp.data


def test_pricing_ctas_carry_plan(client):
    html = client.get("/pricing").data.decode()
    assert "/auth/register?plan=academic" in html
    assert "/auth/register?plan=extended_archive" in html


def test_auth_forms_have_autocomplete(client):
    login_html = client.get("/auth/login").data.decode()
    assert re.search(r'name="email"[^>]*autocomplete="email"|autocomplete="email"[^>]*name="email"', login_html)
    assert 'autocomplete="current-password"' in login_html
    register_html = client.get("/auth/register").data.decode()
    assert 'autocomplete="new-password"' in register_html


def test_login_failure_focuses_password(client):
    register(client)
    client.post("/auth/logout")
    resp = client.post("/auth/login", data={"email": "user@example.com", "password": "wrong-pass"})
    assert re.search(rb'name="password"[^>]*autofocus|autofocus[^>]*name="password"', resp.data)


# --- CSRF / session expiry -------------------------------------------------------

def test_csrf_failure_logged_in_returns_to_form_with_message(client, app):
    register(client)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(
            "/account/profile",
            data={"username": "X Y", "csrf_token": "stale"},
            headers={"Referer": "http://localhost/profile"},
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].split("?")[0].endswith("/profile")
    assert "restore_draft=1" in resp.headers["Location"]
    page = client.get("/profile").data
    assert b"security check expired" in page


def test_csrf_failure_logged_out_goes_to_login_with_next(client, app):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(
            "/papers/new",
            data={"title": "Lost", "csrf_token": "stale"},
            headers={"Referer": "http://localhost/papers/new"},
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code in (302, 303)
    location = resp.headers["Location"]
    assert "/auth/login" in location
    assert "next=" in location and "papers" in location


def test_csrf_failure_json_request_gets_json(client, app):
    register(client)
    model_id = _make_model(app)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(
            f"/models/{model_id}/annotations",
            json={"label": "x", "position": "0 0 0", "normal": "0 1 0"},
            headers={"X-CSRFToken": "stale"},
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code == 400
    assert resp.is_json
    assert resp.get_json().get("error") == "csrf_expired"


def test_csrf_failure_ignores_offsite_referrer(client, app):
    register(client)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(
            "/account/profile",
            data={"username": "X Y", "csrf_token": "stale"},
            headers={"Referer": "https://evil.example.com/phish"},
        )
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code in (302, 303)
    assert "evil.example.com" not in resp.headers["Location"]


# --- New project form --------------------------------------------------------------

def test_new_project_form_does_not_hard_require_source_unit(client):
    register(client)
    html = client.get("/papers/new").data.decode()
    select = re.search(r'<select[^>]*name="source_unit"[^>]*>', html).group(0)
    assert not re.search(r'\srequired(?=[\s>=])', select)
    assert "data-model-required" in select


def test_new_project_without_model_is_created(client, app):
    register(client)
    resp = client.post(
        "/papers/new",
        data={"project_type": "thesis", "workflow_stage": "in_progress", "title": "Model later", "visibility": "private"},
    )
    assert resp.status_code in (302, 303)
    with app.app_context():
        assert Paper.query.filter_by(title="Model later").first() is not None


def test_new_project_form_has_labelled_selects(client):
    register(client)
    html = client.get("/papers/new").data.decode()
    for name in ("project_type", "workflow_stage"):
        tag = re.search(rf'<select[^>]*name="{name}"[^>]*>', html).group(0)
        assert "aria-label=" in tag or "id=" in tag


# --- Upgrade -------------------------------------------------------------------------

def test_invalid_coupon_keeps_code_and_plan(client, app):
    register(client)
    model_id = _make_model(app)
    resp = client.post(f"/models/{model_id}/upgrade/extended_archive", data={"coupon_code": "TYPO10"})
    assert resp.status_code in (302, 303)
    page = client.get(resp.headers["Location"]).data.decode()
    assert 'value="TYPO10"' in page
    assert re.search(r'value="extended_archive"[^>]*checked', page)


# --- Shared UI -------------------------------------------------------------------------

def test_flash_messages_are_dismissable(client):
    register(client)
    html = client.get("/dashboard").data.decode()
    assert "data-flash-close" in html


def test_dashboard_posters_load_lazily(client, app):
    register(client)
    _make_model(app)
    html = client.get("/dashboard").data.decode()
    posters = re.findall(r'<img[^>]*poster\.png[^>]*>', html)
    assert posters, "expected at least one poster thumbnail on the dashboard"
    assert all('loading="lazy"' in tag for tag in posters)
