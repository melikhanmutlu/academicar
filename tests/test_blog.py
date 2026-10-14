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


def test_blog_post_share_tags_use_post_title_and_cover(client):
    body = client.get("/blog/dicom-to-3d-model-guide").get_data(as_text=True)
    assert '<meta property="og:title" content="From DICOM to Interactive 3D' in body
    assert '<meta property="og:type" content="article">' in body
    assert "covers/dicom-to-3d-model-guide.webp" in body.split('property="og:image"')[1].split(">")[0]
    import json
    import re

    blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)]
    posting = next(b for b in blocks if b.get("@type") == "BlogPosting")
    assert posting["image"].endswith("covers/dicom-to-3d-model-guide.webp")
    assert posting["keywords"]


def test_blog_post_without_cover_falls_back_to_default_share_image(client):
    body = client.get("/blog/how-to-add-3d-models-to-research-papers").get_data(as_text=True)
    assert "clinical-spatial-hero.png" in body.split('property="og:image"')[1].split(">")[0]


def test_blog_post_links_related_guides(client):
    body = client.get("/blog/hounsfield-units-ct-thresholding-3d").get_data(as_text=True)
    assert "Related guides" in body
    assert '/blog/dicom-to-3d-model-guide"' in body


def test_sitemap_blog_posts_have_lastmod(client):
    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/blog/dicom-to-3d-model-guide</loc><lastmod>2026-09-24</lastmod>" in body


def test_blog_post_admin_uploaded_image_is_the_cover(app, client):
    from models import BlogPost, db

    with app.app_context():
        db.session.add(BlogPost(slug="uploaded-cover", title="Uploaded cover", body="![c](/blog-images/abc.webp)\n\nText."))
        db.session.commit()
    body = client.get("/blog/uploaded-cover").get_data(as_text=True)
    og_image = body.split('property="og:image"')[1].split(">")[0]
    assert og_image.endswith('/blog-images/abc.webp"')
    assert og_image.split('content="')[1].startswith("http")


def test_refreshed_builtin_post_reports_its_updated_date(app, client, monkeypatch):
    import blog_content
    from app import seed_builtin_blog_posts

    slug = "dicom-to-3d-model-guide"
    with app.app_context():
        seed_builtin_blog_posts(app)
        new_posts = [dict(p, updated="2026-10-20") if p["slug"] == slug else p for p in blog_content.POSTS]
        monkeypatch.setattr(blog_content, "POSTS", new_posts)
        seed_builtin_blog_posts(app)
    body = client.get(f"/blog/{slug}").get_data(as_text=True)
    assert '"dateModified": "2026-10-20"' in body
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert f"/blog/{slug}</loc><lastmod>2026-10-20</lastmod>" in sitemap
