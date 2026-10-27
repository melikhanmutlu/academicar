"""Sign-up bot checks (honeypot, form timing, Turnstile) and the
email-confirmation gate on project creation / model upload."""
import io
import time
from unittest import mock

import signup_guard
from models import User, db
from tests.conftest import create_user, login, valid_ascii_stl_bytes


def _signup_data(client, email="new@example.com", **extra):
    page = client.get("/auth/register")
    html = page.get_data(as_text=True)
    marker = f'name="{signup_guard.TIMING_FIELD}" value="'
    start = html.index(marker) + len(marker)
    stamp = html[start:html.index('"', start)]
    data = {
        "username": "New Person",
        "email": email,
        "password": "password123",
        "confirm": "password123",
        "accept_terms": "y",
        signup_guard.TIMING_FIELD: stamp,
        signup_guard.HONEYPOT_FIELD: "",
    }
    data.update(extra)
    return data


def _enable_bot_checks(app, min_seconds=3):
    app.config.update(SIGNUP_BOT_CHECKS=True, SIGNUP_MIN_SECONDS=min_seconds)


def _user_exists(app, email):
    with app.app_context():
        return User.query.filter_by(email=email).first() is not None


def test_register_page_renders_honeypot_and_stamp(app, client):
    _enable_bot_checks(app)
    html = client.get("/auth/register").get_data(as_text=True)
    assert f'name="{signup_guard.HONEYPOT_FIELD}"' in html
    assert f'name="{signup_guard.TIMING_FIELD}"' in html
    assert "challenges.cloudflare.com" not in html  # no Turnstile without keys


def test_human_signup_passes_bot_checks(app, client):
    _enable_bot_checks(app, min_seconds=0)
    data = _signup_data(client)
    with mock.patch("auth.send_verification_email") as sent:
        resp = client.post("/auth/register", data=data)
    assert resp.status_code == 302
    assert _user_exists(app, "new@example.com")
    sent.assert_called_once()


def test_honeypot_blocks_signup_and_sends_no_email(app, client):
    _enable_bot_checks(app, min_seconds=0)
    data = _signup_data(client, **{signup_guard.HONEYPOT_FIELD: "http://spam.example"})
    with mock.patch("auth.send_verification_email") as sent:
        resp = client.post("/auth/register", data=data)
    assert resp.status_code == 400
    assert "data-signup-blocked" in resp.get_data(as_text=True)
    assert not _user_exists(app, "new@example.com")
    sent.assert_not_called()


def test_missing_stamp_blocks_signup(app, client):
    _enable_bot_checks(app, min_seconds=0)
    data = _signup_data(client)
    data.pop(signup_guard.TIMING_FIELD)
    resp = client.post("/auth/register", data=data)
    assert resp.status_code == 400
    assert not _user_exists(app, "new@example.com")


def test_forged_stamp_blocks_signup(app, client):
    _enable_bot_checks(app, min_seconds=0)
    data = _signup_data(client, **{signup_guard.TIMING_FIELD: "forged.token"})
    assert client.post("/auth/register", data=data).status_code == 400
    assert not _user_exists(app, "new@example.com")


def test_too_fast_submission_blocks_signup(app, client):
    _enable_bot_checks(app, min_seconds=60)
    data = _signup_data(client)
    assert client.post("/auth/register", data=data).status_code == 400
    assert not _user_exists(app, "new@example.com")


def test_stamp_older_than_minimum_passes(app, client):
    _enable_bot_checks(app, min_seconds=3)
    data = _signup_data(client)
    real_time = time.time
    with mock.patch("signup_guard.time.time", side_effect=lambda: real_time() + 5):
        resp = client.post("/auth/register", data=data)
    assert resp.status_code == 302
    assert _user_exists(app, "new@example.com")


def test_turnstile_widget_and_verification(app, client):
    _enable_bot_checks(app, min_seconds=0)
    app.config.update(TURNSTILE_SITE_KEY="site-key", TURNSTILE_SECRET_KEY="secret")
    html = client.get("/auth/register").get_data(as_text=True)
    assert 'data-sitekey="site-key"' in html

    data = _signup_data(client)
    # No token -> blocked without calling Cloudflare.
    with mock.patch("signup_guard._verify_turnstile") as verify:
        assert client.post("/auth/register", data=data).status_code == 400
        verify.assert_not_called()
    # Rejected token -> blocked.
    with mock.patch("signup_guard._verify_turnstile", return_value=False):
        data = _signup_data(client, **{"cf-turnstile-response": "bad"})
        assert client.post("/auth/register", data=data).status_code == 400
    assert not _user_exists(app, "new@example.com")
    # Accepted token -> account created.
    with mock.patch("signup_guard._verify_turnstile", return_value=True) as verify:
        data = _signup_data(client, **{"cf-turnstile-response": "good"})
        assert client.post("/auth/register", data=data).status_code == 302
        assert verify.call_args[0][0] == "good"
    assert _user_exists(app, "new@example.com")


def test_csp_allows_turnstile(app, client):
    csp = client.get("/auth/register").headers.get("Content-Security-Policy", "")
    assert "https://challenges.cloudflare.com" in csp
    assert "frame-src 'self' https://challenges.cloudflare.com" in csp


# --- email-confirmation gate -------------------------------------------------


def _unverified_user(app, email="unverified@example.com"):
    with app.app_context():
        user = create_user(email=email)
        assert user.email_verified_at is None
        return user.id


def test_unverified_user_cannot_create_project(app, client):
    app.config["REQUIRE_EMAIL_VERIFICATION"] = True
    _unverified_user(app)
    login(client, "unverified@example.com")
    resp = client.get("/projects/new")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/dashboard")
    resp = client.post("/projects/new", data={"title": "Spam"}, follow_redirects=True)
    assert "confirm your email address before creating projects" in resp.get_data(as_text=True)
    from models import Paper
    with app.app_context():
        assert Paper.query.count() == 0


def test_unverified_user_cannot_start_chunked_upload(app, client):
    app.config["REQUIRE_EMAIL_VERIFICATION"] = True
    _unverified_user(app)
    login(client, "unverified@example.com")
    resp = client.post("/uploads", json={"filename": "a.stl", "size": 10, "target": {"kind": "new_project"}})
    assert resp.status_code == 403


def test_unverified_user_cannot_upload_model_to_existing_project(app, client):
    from models import Paper

    app.config["REQUIRE_EMAIL_VERIFICATION"] = False
    _unverified_user(app)
    login(client, "unverified@example.com")
    client.post("/projects/new", data={"title": "Legit project", "authors": "A", "year": "2026"})
    with app.app_context():
        paper = Paper.query.first()
        assert paper is not None
        slug = paper.slug
    app.config["REQUIRE_EMAIL_VERIFICATION"] = True
    resp = client.post(
        f"/papers/{slug}/upload-model",
        data={"file": (io.BytesIO(valid_ascii_stl_bytes()), "m.stl"), "compliance_confirmed": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "confirm your email address" in resp.get_data(as_text=True)
    from models import Model3D
    with app.app_context():
        assert Model3D.query.count() == 0


def test_verified_user_can_open_new_project_page(app, client):
    app.config["REQUIRE_EMAIL_VERIFICATION"] = True
    _unverified_user(app)
    with app.app_context():
        from auth import mark_email_verified
        user = User.query.filter_by(email="unverified@example.com").first()
        mark_email_verified(user)
        db.session.commit()
    login(client, "unverified@example.com")
    assert client.get("/projects/new").status_code == 200


def test_admin_users_filter_unverified(app, client):
    app.config["REQUIRE_EMAIL_VERIFICATION"] = True
    with app.app_context():
        from auth import mark_email_verified
        admin = User(email="adm@example.com", username="Adm", is_admin=True)
        admin.set_password("password123")
        mark_email_verified(admin)
        db.session.add(admin)
        ok = User(email="ok@example.com", username="Okay Person")
        ok.set_password("password123")
        mark_email_verified(ok)
        db.session.add(ok)
        bot = User(email="victim@example.com", username="user5x4gdddz342ds")
        bot.set_password("password123")
        db.session.add(bot)
        db.session.commit()
    login(client, "adm@example.com")
    html = client.get("/admin/users?user_role=unverified").get_data(as_text=True)
    assert "victim@example.com" in html
    assert "ok@example.com" not in html
    assert "Unconfirmed" in html
    csv = client.get("/admin/users/export.csv?user_role=unverified").get_data(as_text=True)
    assert "victim@example.com" in csv and "ok@example.com" not in csv


# --- admin bulk delete --------------------------------------------------------


def _bulk_setup(app):
    with app.app_context():
        from auth import mark_email_verified
        admin = User(email="adm@example.com", username="Adm", is_admin=True)
        admin.set_password("password123")
        mark_email_verified(admin)
        other_admin = User(email="adm2@example.com", username="Adm2", is_admin=True)
        other_admin.set_password("password123")
        keep = User(email="keep@example.com", username="Keep")
        keep.set_password("password123")
        bots = [User(email=f"bot{i}@example.com", username="user5x4gdddz342ds") for i in range(3)]
        for bot in bots:
            bot.set_password("password123")
        db.session.add_all([admin, other_admin, keep, *bots])
        db.session.commit()
        return admin.id, other_admin.id, keep.id, [b.id for b in bots]


def test_users_page_has_bulk_checkboxes_except_admins(app, client):
    admin_id, other_admin_id, keep_id, bot_ids = _bulk_setup(app)
    login(client, "adm@example.com")
    html = client.get("/admin/users").get_data(as_text=True)
    assert "data-users-bulk" in html
    for uid in [keep_id, *bot_ids]:
        assert f'name="user_ids" value="{uid}"' in html
    for uid in [admin_id, other_admin_id]:
        assert f'name="user_ids" value="{uid}"' not in html


def test_bulk_delete_removes_selected_members_and_skips_admins(app, client):
    admin_id, other_admin_id, keep_id, bot_ids = _bulk_setup(app)
    login(client, "adm@example.com")
    resp = client.post(
        "/admin/users/bulk-delete",
        data={"user_ids": [*map(str, bot_ids), str(admin_id), str(other_admin_id), "junk"]},
        follow_redirects=True,
    )
    assert "Deleted 3 accounts." in resp.get_data(as_text=True)
    with app.app_context():
        remaining = {u.id for u in User.query.all()}
    assert remaining == {admin_id, other_admin_id, keep_id}


def test_bulk_delete_requires_admin(app, client):
    _admin_id, _other, keep_id, bot_ids = _bulk_setup(app)
    login(client, "keep@example.com")
    resp = client.post("/admin/users/bulk-delete", data={"user_ids": [str(bot_ids[0])]})
    assert resp.status_code in {302, 403, 404}
    with app.app_context():
        assert db.session.get(User, bot_ids[0]) is not None


def test_bulk_delete_with_nothing_selected(app, client):
    _bulk_setup(app)
    login(client, "adm@example.com")
    resp = client.post("/admin/users/bulk-delete", data={}, follow_redirects=True)
    assert "Select at least one user to delete." in resp.get_data(as_text=True)
