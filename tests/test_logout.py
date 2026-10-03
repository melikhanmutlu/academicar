"""Logging out must end the session even when "Remember me" was ticked."""
from tests.conftest import register


def _logged_in(client):
    resp = client.get("/dashboard", follow_redirects=False)
    return resp.status_code == 200


def test_logout_without_remember_me(client):
    register(client)
    client.post("/auth/logout")
    client.post("/auth/login", data={"email": "user@example.com", "password": "password123"})
    assert _logged_in(client)
    client.post("/auth/logout")
    assert not _logged_in(client)


def test_logout_with_remember_me_clears_remember_cookie(client):
    register(client)
    client.post("/auth/logout")
    client.post("/auth/login", data={"email": "user@example.com", "password": "password123", "remember": "y"})
    assert client.get_cookie("remember_token") is not None
    assert _logged_in(client)
    resp = client.post("/auth/logout")
    assert resp.status_code in (302, 303)
    assert client.get_cookie("remember_token") is None
    assert not _logged_in(client)


def test_logout_clears_remember_cookie_left_from_an_earlier_login(client):
    """A remember-me cookie from an earlier login must not survive a later
    logout, even when the latest login did not tick "Remember me"."""
    register(client)
    client.post("/auth/logout")
    client.post("/auth/login", data={"email": "user@example.com", "password": "password123", "remember": "y"})
    old_cookie = client.get_cookie("remember_token").value
    client.post("/auth/logout")
    client.set_cookie("remember_token", old_cookie)  # e.g. restored by another tab / older session
    client.post("/auth/login", data={"email": "user@example.com", "password": "password123"})
    assert _logged_in(client)
    client.post("/auth/logout")
    assert not _logged_in(client)
