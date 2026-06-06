"""Tests for blog rich content: admin image upload/serving and Markdown
tables + images rendering in a published post."""
import io

from models import BlogPost, User, db


def _login_admin(client, app, email="imgadmin@example.com"):
    from tests.conftest import login

    with app.app_context():
        u = User(email=email, username="Admin", is_admin=True)
        u.set_password("password123")
        db.session.add(u)
        db.session.commit()
    return login(client, email=email, password="password123")


def _upload(client, filename="photo.png", data=b"\x89PNG\r\n\x1a\nfake-image-bytes"):
    return client.post(
        "/admin/blog/upload-image",
        data={"image": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
    )


def test_admin_can_upload_image_and_serve_it(client, app):
    _login_admin(client, app)
    resp = _upload(client)
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["url"].startswith("/blog-images/")
    assert payload["markdown"] == f"![]({payload['url']})"

    served = client.get(payload["url"])
    assert served.status_code == 200
    assert served.data == b"\x89PNG\r\n\x1a\nfake-image-bytes"


def test_image_upload_requires_admin(client, app):
    from tests.conftest import register

    register(client)  # ordinary logged-in user
    assert _upload(client).status_code == 403


def test_image_upload_rejects_unsupported_type(client, app):
    _login_admin(client, app)
    resp = _upload(client, filename="evil.svg", data=b"<svg onload=alert(1)>")
    assert resp.status_code == 400
    assert "Unsupported" in resp.get_json()["error"]


def test_serve_blog_image_404_for_missing(client):
    assert client.get("/blog-images/does-not-exist.png").status_code == 404


def test_post_body_renders_table_and_image(client, app):
    _login_admin(client, app)
    body = (
        "## Results\n\n"
        "| Metric | Value |\n"
        "|--------|-------|\n"
        "| Speed  | Fast  |\n"
        "| Cost   | Low   |\n\n"
        "![diagram](/blog-images/example.png)\n"
    )
    client.post("/admin/blog/create", data={"title": "Rich Post", "body": body, "is_published": "on"})
    with app.app_context():
        slug = BlogPost.query.filter_by(title="Rich Post").first().slug

    html = client.get(f"/blog/{slug}").get_data(as_text=True)
    assert "<table>" in html
    assert "<th>Metric</th>" in html
    assert '<img alt="diagram" src="/blog-images/example.png"' in html or 'src="/blog-images/example.png"' in html
