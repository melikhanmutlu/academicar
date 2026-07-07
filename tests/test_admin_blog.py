"""Tests for admin-managed blog posts (create / edit / publish / delete) and
how they surface on the public blog alongside the built-in code posts."""
from models import BlogPost, User, db


def _make_admin(app, email="admin@example.com", password="password123"):
    with app.app_context():
        u = User(email=email, username="Admin", is_admin=True)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()


def _login_admin(client, app):
    from tests.conftest import login

    _make_admin(app)
    return login(client, email="admin@example.com", password="password123")


def _slug_for(app, title):
    with app.app_context():
        post = BlogPost.query.filter_by(title=title).first()
        return post.slug if post else None


def test_admin_can_create_blog_post_and_it_appears_public(client, app):
    _login_admin(client, app)
    resp = client.post(
        "/admin/blog/create",
        data={
            "title": "Spatial Computing in the Lab",
            "description": "How spatial computing helps research.",
            "body": "## Why it matters\n\nInteractive 3D **changes** everything.",
            "tags": "research, spatial",
            "read_minutes": "4",
            "is_published": "on",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)

    slug = _slug_for(app, "Spatial Computing in the Lab")
    assert slug == "spatial-computing-in-the-lab"

    listing = client.get("/blog").get_data(as_text=True)
    assert "Spatial Computing in the Lab" in listing

    post_html = client.get(f"/blog/{slug}").get_data(as_text=True)
    assert "<h2" in post_html  # markdown rendered
    assert "<strong>changes</strong>" in post_html
    assert "BlogPosting" in post_html  # schema still emitted


def test_built_in_code_posts_still_listed(client):
    listing = client.get("/blog").get_data(as_text=True)
    assert "How to Add Interactive 3D Models" in listing


def test_builtin_post_seeded_into_db_and_editable_from_admin(client, app):
    _login_admin(client, app)
    # Visiting the admin blog page self-heals the built-in posts into the DB
    # (same precedent as seed_license_plans on the pricing page).
    listing = client.get("/admin/blog").get_data(as_text=True)
    assert "How to Add Interactive 3D Models" in listing

    with app.app_context():
        seeded = BlogPost.query.filter_by(slug="how-to-add-3d-models-to-research-papers").first()
        assert seeded is not None
        assert seeded.is_published is True
        post_id = seeded.id

    client.post(
        f"/admin/blog/{post_id}/update",
        data={
            "title": "How to Add Interactive 3D Models to Your Research Paper",
            "body": "## Updated\n\nedited straight from admin",
            "is_published": "on",
        },
    )
    public = client.get("/blog/how-to-add-3d-models-to-research-papers").get_data(as_text=True)
    assert "edited straight from admin" in public


def test_non_admin_cannot_create_or_view_admin_blog(client, app):
    from tests.conftest import login, register

    register(client)  # ordinary, non-admin user (logged in)
    assert client.get("/admin/blog").status_code == 403
    resp = client.post(
        "/admin/blog/create",
        data={"title": "x", "body": "y", "is_published": "on"},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    with app.app_context():
        assert BlogPost.query.count() == 0


def test_admin_blog_page_renders_for_admin(client, app):
    _login_admin(client, app)
    assert client.get("/admin/blog").status_code == 200


def test_unpublished_post_is_hidden_publicly(client, app):
    _login_admin(client, app)
    client.post(
        "/admin/blog/create",
        data={"title": "Draft Article", "body": "## hi\n\ntext", "is_published": "on"},
    )
    slug = _slug_for(app, "Draft Article")
    with app.app_context():
        post_id = BlogPost.query.filter_by(slug=slug).first().id

    # Toggle to draft -> 404 publicly and absent from the listing.
    client.post(f"/admin/blog/{post_id}/publish")
    assert client.get(f"/blog/{slug}").status_code == 404
    assert "Draft Article" not in client.get("/blog").get_data(as_text=True)

    # Toggle back to published -> visible again.
    client.post(f"/admin/blog/{post_id}/publish")
    assert client.get(f"/blog/{slug}").status_code == 200


def test_admin_can_update_post_body(client, app):
    _login_admin(client, app)
    client.post(
        "/admin/blog/create",
        data={"title": "Editable Post", "body": "original body text", "is_published": "on"},
    )
    slug = _slug_for(app, "Editable Post")
    with app.app_context():
        post_id = BlogPost.query.filter_by(slug=slug).first().id

    assert "original body text" in client.get(f"/blog/{slug}").get_data(as_text=True)

    client.post(
        f"/admin/blog/{post_id}/update",
        data={"title": "Editable Post", "body": "## Updated\n\nbrand new content", "is_published": "on"},
    )
    updated = client.get(f"/blog/{slug}").get_data(as_text=True)
    assert "brand new content" in updated
    assert "original body text" not in updated


def test_admin_can_delete_post(client, app):
    _login_admin(client, app)
    client.post("/admin/blog/create", data={"title": "Temp Post", "body": "body", "is_published": "on"})
    slug = _slug_for(app, "Temp Post")
    with app.app_context():
        post_id = BlogPost.query.filter_by(slug=slug).first().id

    assert client.get(f"/blog/{slug}").status_code == 200
    client.post(f"/admin/blog/{post_id}/delete")
    assert client.get(f"/blog/{slug}").status_code == 404
    with app.app_context():
        assert db.session.get(BlogPost, post_id) is None


def test_create_requires_title_and_body(client, app):
    _login_admin(client, app)
    resp = client.post("/admin/blog/create", data={"title": "", "body": "", "is_published": "on"}, follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        # The redirect lands on /admin/blog, which self-heals the 6 built-in
        # posts into the DB (same precedent as the pricing page and
        # seed_license_plans) — but the invalid submission itself must not
        # have created anything.
        assert BlogPost.query.count() == 6
        assert BlogPost.query.filter_by(title="").count() == 0
