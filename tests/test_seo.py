"""Tests for SEO surfaces: robots.txt, sitemap.xml, canonical tags, JSON-LD
structured data, and the new FAQ / About / Contact pages."""


def test_robots_txt(client):
    client.application.config["APP_ENV"] = "pilot"
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert "text/plain" in resp.content_type
    body = resp.get_data(as_text=True)
    assert "User-agent: *" in body
    assert "Disallow: /admin" in body
    assert "Sitemap:" in body
    assert "/sitemap.xml" in body


def test_sitemap_xml(client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert "xml" in resp.content_type
    body = resp.get_data(as_text=True)
    assert "<urlset" in body
    assert "/pricing" in body
    assert "/faq" in body
    assert "/about" in body


def test_sitemap_includes_public_papers_only(client, app):
    import uuid

    from models import Paper, User, db

    with app.app_context():
        user = User(email="seo@example.com", username="SEO")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        public = Paper(title="Public", slug="public-paper", user_id=user.id, is_public=True)
        private = Paper(title="Private", slug="private-paper", user_id=user.id, is_public=False)
        db.session.add_all([public, private])
        db.session.commit()

    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "public-paper" in body
    assert "private-paper" not in body


def test_landing_has_canonical_and_org_schema(client):
    body = client.get("/").get_data(as_text=True)
    assert 'rel="canonical"' in body
    assert '"@type": "Organization"' in body
    assert '"@type": "WebSite"' in body


def test_faq_page_has_faqpage_schema(client):
    resp = client.get("/faq")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "FAQPage" in body
    assert "What is AcademicAR" in body


def test_pricing_has_softwareapplication_schema(client):
    body = client.get("/pricing").get_data(as_text=True)
    assert "SoftwareApplication" in body


def test_about_page(client):
    resp = client.get("/about")
    assert resp.status_code == 200
    assert "About AcademicAR" in resp.get_data(as_text=True)


def test_contact_hidden_by_default(client):
    # CONTACT_ENABLED defaults off — the page is hidden so we don't collect mail.
    assert client.get("/contact").status_code == 404
    assert client.post("/contact", data={"name": "Ada", "email": "a@e.com", "message": "hi"}).status_code == 404


def test_contact_page_and_submission_when_enabled(client):
    client.application.config["CONTACT_ENABLED"] = True
    assert client.get("/contact").status_code == 200
    resp = client.post(
        "/contact",
        data={"name": "Ada", "email": "ada@example.com", "message": "Hello, I have a question."},
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_contact_requires_fields_when_enabled(client):
    client.application.config["CONTACT_ENABLED"] = True
    resp = client.post("/contact", data={"name": "", "email": "", "message": ""}, follow_redirects=True)
    assert resp.status_code == 200


def test_robots_txt_blocks_indexing_outside_live_envs(client):
    client.application.config["APP_ENV"] = "development"
    body = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /" in body
    assert "Disallow: /admin" not in body


def test_google_site_verification_meta_absent_by_default(client):
    assert "google-site-verification" not in client.get("/").get_data(as_text=True)


def test_google_site_verification_meta_present_when_configured(client):
    client.application.config["GOOGLE_SITE_VERIFICATION"] = "abc123token"
    body = client.get("/").get_data(as_text=True)
    assert '<meta name="google-site-verification" content="abc123token">' in body


def test_favicon_link_present(client):
    body = client.get("/").get_data(as_text=True)
    assert 'rel="icon"' in body and "favicon.svg" in body


def test_paper_public_meta_description_uses_abstract(client, app):
    from models import Paper, User, db

    with app.app_context():
        user = User(email="seo2@example.com", username="SEO2")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(Paper(
            title="Meta Description Paper", slug="meta-desc-paper", user_id=user.id,
            is_public=True, abstract="A distinctive abstract sentence for meta description testing.",
        ))
        db.session.commit()

    body = client.get("/p/meta-desc-paper").get_data(as_text=True)
    # Exact tag match: this is the precise regression guard — if meta_description
    # still fell back to the generic site-wide copy, this content value would differ.
    assert '<meta name="description" content="A distinctive abstract sentence for meta description testing.">' in body


def test_demo_viewer_has_seo_tags(client):
    body = client.get("/demo/mitochondria/ar").get_data(as_text=True)
    assert 'name="description"' in body
    assert 'rel="canonical"' in body
    assert 'property="og:type" content="website"' in body
