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


def test_every_discipline_has_its_illustration_and_tools():
    import os

    for d in all_disciplines():
        for key in ("image", "thumb"):
            assert os.path.isfile(os.path.join("static", d[key])), (d["slug"], key)
        assert d["image_alt"], d["slug"]
        assert len(d["tools"]) == 3, d["slug"]
        for tool in d["tools"]:
            assert os.path.isfile(os.path.join("static", tool["image"])), tool["image"]


def test_discipline_page_shows_illustration_tools_and_guides(client):
    body = client.get("/ar-for-anatomy").get_data(as_text=True)
    anatomy = get_discipline("anatomy")
    assert "images/use-cases/anatomy.webp" in body
    assert str(escape(anatomy["image_alt"])) in body
    # The share preview uses the field illustration, not the site default.
    assert 'property="og:image" content="http' in body
    assert "images/use-cases/anatomy.webp" in body.split('property="og:image"')[1].split(">")[0]
    assert '"primaryImageOfPage"' in body
    for tool in anatomy["tools"]:
        assert tool["title"] in body
    # Related blog guides are linked for internal linking.
    for slug in anatomy["guide_slugs"]:
        assert f"/blog/{slug}" in body


def test_hub_cards_show_thumbnails_and_share_image(client):
    body = client.get("/ar-for").get_data(as_text=True)
    for d in all_disciplines():
        assert d["thumb"] in body
    assert "images/use-cases/" in body.split('property="og:image"')[1].split(">")[0]


def test_related_disciplines_rotate_through_the_cluster():
    slugs = discipline_slugs()
    # Every field is linked from at least one other field's page.
    linked = {rel["slug"] for s in slugs for rel in related_disciplines(s)}
    assert linked == set(slugs)
    assert all(s not in {r["slug"] for r in related_disciplines(s)} for s in slugs)


def test_landing_links_each_field(client):
    body = client.get("/").get_data(as_text=True)
    for slug in discipline_slugs():
        assert f"/ar-for-{slug}" in body
