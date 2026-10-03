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
