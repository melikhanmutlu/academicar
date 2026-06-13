"""Tests for the password reset flow."""


def test_forgot_password_generic_response(client):
    # Unknown email must still get the generic response (no account enumeration).
    resp = client.post(
        "/auth/forgot-password",
        data={"email": "nobody@example.com"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"sent a password reset link" in resp.data


def test_reset_password_flow(client, app):
    from auth import generate_password_reset_token
    from tests.conftest import create_user, login

    with app.app_context():
        user = create_user(email="reset@example.com", password="oldpassword1")
        token = generate_password_reset_token(user)

    assert client.get(f"/auth/reset-password/{token}").status_code == 200

    resp = client.post(
        f"/auth/reset-password/{token}",
        data={"password": "newpassword1", "confirm": "newpassword1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # Old password no longer works; new one does.
    bad = login(client, email="reset@example.com", password="oldpassword1", follow_redirects=False)
    assert bad.status_code == 200  # re-render = failed login
    good = login(client, email="reset@example.com", password="newpassword1", follow_redirects=False)
    assert good.status_code == 302  # redirect = success


def test_reset_token_is_single_use(client, app):
    """A reset link cannot be replayed once the password has been changed."""
    from auth import generate_password_reset_token, verify_password_reset_token
    from tests.conftest import create_user

    with app.app_context():
        user = create_user(email="single@example.com", password="oldpassword1")
        token = generate_password_reset_token(user)

    resp = client.post(
        f"/auth/reset-password/{token}",
        data={"password": "newpassword1", "confirm": "newpassword1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # The same token no longer verifies (fingerprint now mismatches).
    with app.app_context():
        assert verify_password_reset_token(token) is None

    # And re-POSTing it is rejected (redirected back to forgot-password).
    replay = client.post(
        f"/auth/reset-password/{token}",
        data={"password": "attacker1x", "confirm": "attacker1x"},
        follow_redirects=False,
    )
    assert replay.status_code == 302
    assert "/auth/forgot-password" in replay.headers["Location"]


def test_reset_password_rejects_invalid_token(client):
    resp = client.get("/auth/reset-password/not-a-real-token", follow_redirects=False)
    assert resp.status_code == 302  # redirected back to forgot-password


def test_reset_password_rejects_mismatched_confirmation(client, app):
    from auth import generate_password_reset_token
    from tests.conftest import create_user, login

    with app.app_context():
        user = create_user(email="mismatch@example.com", password="oldpassword1")
        token = generate_password_reset_token(user)

    resp = client.post(
        f"/auth/reset-password/{token}",
        data={"password": "newpassword1", "confirm": "different1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # Password unchanged: old one still logs in.
    good = login(client, email="mismatch@example.com", password="oldpassword1", follow_redirects=False)
    assert good.status_code == 302
