from auth import is_safe_redirect_url
from app import validate_paper_form, validate_pdf_file, validate_stl_file
from models import User
from url_helpers import public_url


def test_login_rejects_external_next(client, app):
    from tests.conftest import create_user

    with app.app_context():
        create_user()

    response = client.post(
        "/auth/login?next=https://evil.example/path",
        data={"email": "user@example.com", "password": "password123"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_safe_redirect_allows_local_path(client):
    with client.application.test_request_context("/auth/login", base_url="http://localhost"):
        assert is_safe_redirect_url("/dashboard")
        assert not is_safe_redirect_url("https://evil.example/dashboard")


def test_public_url_uses_configured_site_url(app):
    app.config["SITE_URL"] = "https://academic.example/base"
    with app.test_request_context("/view/abc", base_url="http://evil.example"):
        assert public_url("view_model", model_id="abc") == "https://academic.example/base/view/abc"


def test_logout_requires_post(client, app):
    from tests.conftest import create_user, login

    with app.app_context():
        create_user()
    login(client)

    assert client.get("/auth/logout").status_code == 405
    assert client.post("/auth/logout", follow_redirects=False).status_code == 302


def test_google_callback_rejects_unverified_email(client, app, monkeypatch):
    import auth as auth_module

    class FakeGoogle:
        def authorize_access_token(self):
            return {
                "userinfo": {
                    "sub": "google-user-1",
                    "email": "new@example.com",
                    "email_verified": False,
                    "name": "New User",
                }
            }

    monkeypatch.setattr(auth_module.oauth, "google", FakeGoogle(), raising=False)
    response = client.get("/auth/google/callback", follow_redirects=False)

    assert response.status_code == 302
    with app.app_context():
        assert User.query.filter_by(email="new@example.com").first() is None


def test_paper_form_rejects_invalid_year():
    _, errors = validate_paper_form({"title": "Paper", "year": "1800"})
    assert errors


def test_stl_header_validation():
    from tests.conftest import valid_ascii_stl_bytes
    from pathlib import Path
    from uuid import uuid4

    test_dir = Path("tests_runtime") / uuid4().hex
    test_dir.mkdir(parents=True, exist_ok=True)
    valid_path = test_dir / "valid.stl"
    invalid_path = test_dir / "invalid.stl"
    valid_path.write_bytes(valid_ascii_stl_bytes())
    invalid_path.write_bytes(b"not an stl")

    assert validate_stl_file(str(valid_path)) == []
    assert validate_stl_file(str(invalid_path))


def test_pdf_header_validation():
    from pathlib import Path
    from uuid import uuid4

    test_dir = Path("tests_runtime") / uuid4().hex
    test_dir.mkdir(parents=True, exist_ok=True)
    valid_path = test_dir / "valid.pdf"
    invalid_path = test_dir / "invalid.pdf"
    valid_path.write_bytes(b"%PDF-1.4\n")
    invalid_path.write_bytes(b"hello")

    assert validate_pdf_file(str(valid_path)) == []
    assert validate_pdf_file(str(invalid_path))
