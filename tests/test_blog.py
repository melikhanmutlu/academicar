"""Tests for the blog (list, post rendering, schema, sitemap inclusion)."""
import json
import re


def test_blog_index_lists_posts(client):
    resp = client.get("/blog")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "The AcademicAR blog" in body
    assert "How to Add Interactive 3D Models" in body


def test_blog_post_renders_markdown_and_schema(client):
    resp = client.get("/blog/how-to-add-3d-models-to-research-papers")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'rel="canonical"' in body
    assert "<h2" in body  # markdown headings rendered to HTML
    assert "BlogPosting" in body
    assert "BreadcrumbList" in body


def test_blog_post_json_ld_is_valid(client):
    body = client.get("/blog/qr-codes-on-academic-posters-guide").get_data(as_text=True)
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
    types = [json.loads(b).get("@type") for b in blocks]
    assert "BlogPosting" in types
    assert "BreadcrumbList" in types


def test_blog_unknown_slug_404(client):
    assert client.get("/blog/this-post-does-not-exist").status_code == 404


def test_sitemap_includes_blog(client):
    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/blog" in body
    assert "how-to-add-3d-models-to-research-papers" in body


def test_seed_refreshes_unedited_builtin_post_from_code(app, monkeypatch):
    """A built-in post that nobody edited picks up later changes to its code body."""
    import blog_content
    from app import seed_builtin_blog_posts
    from models import BlogPost, db

    slug = blog_content.get_all_posts()[0]["slug"]
    with app.app_context():
        seed_builtin_blog_posts(app)
        row = BlogPost.query.filter_by(slug=slug).one()
        seeded_at = row.updated_at
        new_posts = [dict(p, body=p["body"] + "\nNew paragraph.") if p["slug"] == slug else p for p in blog_content.POSTS]
        monkeypatch.setattr(blog_content, "POSTS", new_posts)
        seed_builtin_blog_posts(app)
        db.session.expire_all()
        row = BlogPost.query.filter_by(slug=slug).one()
        assert row.body.endswith("New paragraph.")
        assert row.updated_at == seeded_at  # still counts as unedited


def test_seed_keeps_admin_edits_to_builtin_post(app, monkeypatch):
    import blog_content
    from app import seed_builtin_blog_posts
    from models import BlogPost, db

    slug = blog_content.get_all_posts()[0]["slug"]
    with app.app_context():
        seed_builtin_blog_posts(app)
        BlogPost.query.filter_by(slug=slug).one().body = "Edited by an admin."
        db.session.commit()
        new_posts = [dict(p, body=p["body"] + "\nNew paragraph.") if p["slug"] == slug else p for p in blog_content.POSTS]
        monkeypatch.setattr(blog_content, "POSTS", new_posts)
        seed_builtin_blog_posts(app)
        db.session.expire_all()
        assert BlogPost.query.filter_by(slug=slug).one().body == "Edited by an admin."
