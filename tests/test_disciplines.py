"""Tests for the programmatic discipline landing pages (/ar-for-<field>) and
the use-cases hub (/ar-for): routing, SEO structured data, sitemap inclusion,
and graceful 404 for unknown fields."""

from markupsafe import escape

from discipline_content import all_disciplines, discipline_slugs, get_discipline, related_disciplines


def test_hub_page(client):
    resp = client.get("/ar-for")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Every discipline is linked from the hub (names are HTML-escaped by Jinja).
    for d in all_disciplines():
        assert f"AR for {escape(d['name'])}" in body
        assert f"/ar-for-{d['slug']}" in body
    assert '"@type": "ItemList"' in body
    assert 'rel="canonical"' in body


def test_each_discipline_page_renders_with_seo(client):
    for slug in discipline_slugs():
        resp = client.get(f"/ar-for-{slug}")
        assert resp.status_code == 200, slug
        body = resp.get_data(as_text=True)
        assert "FAQPage" in body, slug
        assert "BreadcrumbList" in body, slug
        assert 'rel="canonical"' in body, slug
        # The page title comes from the discipline data, not the generic default
        # (the title is HTML-escaped inside the <title> tag).
        assert str(escape(get_discipline(slug)["title"])) in body, slug


def test_hyphenated_slug_resolves(client):
    # "stem-education" contains a hyphen; the route must still match it.
    assert client.get("/ar-for-stem-education").status_code == 200


def test_unknown_discipline_returns_404(client):
    assert client.get("/ar-for-nonsense").status_code == 404


def test_discipline_pages_cross_link(client):
    body = client.get("/ar-for-anatomy").get_data(as_text=True)
    # Related siblings link back into the cluster.
    for rel in related_disciplines("anatomy"):
        assert f"/ar-for-{rel['slug']}" in body


def test_og_title_is_overridden_per_page(client):
    body = client.get("/ar-for-chemistry").get_data(as_text=True)
    # The per-page og:title should reflect the discipline, not the site default.
    assert 'property="og:title" content="AcademicAR - 3D and AR models' not in body
    assert "molecular and materials chemistry" in body


def test_sitemap_includes_disciplines_and_hub(client):
    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "<loc>" in body
    assert "/ar-for</loc>" in body
    for slug in discipline_slugs():
        assert f"/ar-for-{slug}</loc>" in body


def test_robots_allows_discipline_pages(client):
    body = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /ar-for" not in body


def test_footer_links_to_use_cases(client):
    # Sitewide internal link from the footer drives crawl + link equity to the hub.
    body = client.get("/about").get_data(as_text=True)
    assert "/ar-for" in body
    assert ">Use cases<" in body


def test_titles_and_descriptions_are_unique(client):
    titles = {d["title"] for d in all_disciplines()}
    descs = {d["meta_description"] for d in all_disciplines()}
    assert len(titles) == len(discipline_slugs())
    assert len(descs) == len(discipline_slugs())
