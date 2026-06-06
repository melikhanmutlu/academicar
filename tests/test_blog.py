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
