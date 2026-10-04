"""Password sign-ups confirm their email address."""
from datetime import UTC, datetime

from models import User, db
from tests.conftest import login, register


def _capture_mail(monkeypatch):
    sent = []
    monkeypatch.setattr("utils.email.send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def _link(body):
    return next(line.strip() for line in body.splitlines() if "/auth/verify-email/" in line)


def test_signup_sends_confirmation_link_and_shows_banner(client, app, monkeypatch):
    sent = _capture_mail(monkeypatch)
    html = register(client).get_data(as_text=True)
    assert "sent a confirmation link to user@example.com" in html
    assert len(sent) == 1 and sent[0][0] == "user@example.com"
    assert "confirm your email" in sent[0][1].lower()
    with app.app_context():
        assert User.query.one().email_verified_at is None
    assert "Confirm your email address" in client.get("/dashboard").get_data(as_text=True)

    path = "/" + _link(sent[0][2]).split("/", 3)[3]
    html = client.get(path, follow_redirects=True).get_data(as_text=True)
    assert "Your email address is confirmed." in html
    assert "data-verify-email-notice" not in html
    with app.app_context():
        assert User.query.one().email_verified_at is not None


def test_link_works_when_logged_out_and_rejects_tampering(client, app):
    from auth import generate_email_verification_token

    register(client)
    with app.app_context():
        token = generate_email_verification_token(User.query.one())
    client.post("/auth/logout")
    assert b"not valid" in client.get("/auth/verify-email/garbage", follow_redirects=True).data
    assert b"is confirmed" in client.get(f"/auth/verify-email/{token}", follow_redirects=True).data


def test_link_for_an_old_address_stops_working_after_email_change(client, app):
    from auth import generate_email_verification_token

    register(client)
    with app.app_context():
        user = User.query.one()
        token = generate_email_verification_token(user)
        user.email = "changed@example.com"
        db.session.commit()
    assert b"no longer valid" in client.get(f"/auth/verify-email/{token}", follow_redirects=True).data
    with app.app_context():
        assert User.query.one().email_verified_at is None


def test_resend_sends_a_new_link(client, monkeypatch):
    register(client)
    sent = _capture_mail(monkeypatch)
    html = client.post("/auth/verify-email/resend", follow_redirects=True).get_data(as_text=True)
    assert "sent a new confirmation link" in html
    assert len(sent) == 1 and "/auth/verify-email/" in sent[0][2]


def test_password_reset_link_confirms_the_address(client, app):
    from auth import generate_password_reset_token

    register(client)
    client.post("/auth/logout")
    with app.app_context():
        token = generate_password_reset_token(User.query.one())
    client.post(f"/auth/reset-password/{token}", data={"password": "newpassword123", "confirm": "newpassword123"})
    with app.app_context():
        assert User.query.one().email_verified_at is not None


def test_google_sign_in_on_an_unconfirmed_account_drops_the_squatted_password(client, app, monkeypatch):
    """Someone registered the address with a password but never confirmed it;
    the real owner then signs in with Google. The squatter's password must
    not keep working."""
    import auth as auth_module

    register(client, email="owner@example.com")
    client.post("/auth/logout")

    class FakeGoogle:
        def authorize_access_token(self):
            return {"userinfo": {"sub": "g-owner", "email": "owner@example.com", "email_verified": True, "name": "Owner"}}

    monkeypatch.setattr(auth_module.oauth, "google", FakeGoogle(), raising=False)
    client.get("/auth/google/callback")
    with app.app_context():
        user = User.query.filter_by(email="owner@example.com").one()
        assert user.password_hash is None
        assert user.email_verified_at is not None
    client.post("/auth/logout")
    response = login(client, email="owner@example.com")
    assert b"Invalid" in response.data or b"invalid" in response.data


def test_google_sign_in_keeps_the_password_of_a_confirmed_account(client, app, monkeypatch):
    import auth as auth_module

    register(client, email="owner@example.com")
    with app.app_context():
        User.query.one().email_verified_at = datetime.now(UTC)
        db.session.commit()
    client.post("/auth/logout")

    class FakeGoogle:
        def authorize_access_token(self):
            return {"userinfo": {"sub": "g-owner", "email": "owner@example.com", "email_verified": True, "name": "Owner"}}

    monkeypatch.setattr(auth_module.oauth, "google", FakeGoogle(), raising=False)
    client.get("/auth/google/callback")
    with app.app_context():
        assert User.query.one().password_hash is not None
