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


def test_blog_post_without_cover_falls_back_to_default_share_image(app, client):
    from models import BlogPost, db

    with app.app_context():
        db.session.add(BlogPost(slug="no-cover", title="No cover", body="Just text."))
        db.session.commit()
    body = client.get("/blog/no-cover").get_data(as_text=True)
    assert "clinical-spatial-hero.png" in body.split('property="og:image"')[1].split(">")[0]


def test_blog_post_links_related_guides(client):
    body = client.get("/blog/hounsfield-units-ct-thresholding-3d").get_data(as_text=True)
    assert "Related guides" in body
    assert '/blog/dicom-to-3d-model-guide"' in body


def test_sitemap_blog_posts_have_lastmod(client):
    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/blog/dicom-to-3d-model-guide</loc><lastmod>2026-10-19</lastmod>" in body


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


def test_faq_items_parse_questions_and_skip_the_closing_cta():
    from blog_content import faq_items

    body = (
        "Intro.\n\n## Frequently asked questions\n\n"
        "### Does **GLB** keep `file_names`?\n\nYes, see [the guide](/blog/x).\n\n"
        "### Second?\n\n- One\n- Two\n\n"
        "*[Create a free account](/auth/register) today.*\n"
    )
    assert faq_items(body) == [
        {"question": "Does GLB keep file_names?", "answer": "Yes, see the guide."},
        {"question": "Second?", "answer": "One Two"},
    ]
    assert faq_items("## Other heading\n\n### Q?\n\nA.") == []


def test_faq_section_stops_at_the_next_h2():
    from blog_content import faq_items

    body = "## FAQ\n\n### Q?\n\nA.\n\n## More\n\n### Not a question\n\nText."
    assert faq_items(body) == [{"question": "Q?", "answer": "A."}]


def test_blog_post_with_faq_has_faqpage_schema(client):
    body = client.get("/blog/convert-obj-fbx-to-glb").get_data(as_text=True)
    blocks = [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)]
    faq = next(b for b in blocks if b.get("@type") == "FAQPage")
    assert len(faq["mainEntity"]) >= 3
    first = faq["mainEntity"][0]
    assert first["@type"] == "Question" and first["acceptedAnswer"]["text"]


def test_blog_post_without_faq_has_no_faqpage_schema(app, client):
    from models import BlogPost, db

    with app.app_context():
        db.session.add(BlogPost(slug="plain-post", title="Plain", body="## Heading\n\nText."))
        db.session.commit()
    body = client.get("/blog/plain-post").get_data(as_text=True)
    assert "FAQPage" not in body


def test_builtin_posts_have_cover_faq_and_valid_internal_links():
    import os

    from blog_content import POSTS, faq_items

    slugs = {p["slug"] for p in POSTS}
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for post in POSTS:
        cover = f"/static/images/blog/covers/{post['slug']}.webp"
        assert cover in post["body"], post["slug"]
        assert os.path.exists(os.path.join(root, cover.lstrip("/"))), post["slug"]
        assert len(faq_items(post["body"])) >= 3, post["slug"]
        for target in re.findall(r"\]\(/blog/([^)#?]+)\)", post["body"]):
            assert target in slugs, (post["slug"], target)
        for image in re.findall(r"\]\((/static/images/blog/[^)]+)\)", post["body"]):
            assert os.path.exists(os.path.join(root, image.lstrip("/"))), (post["slug"], image)

