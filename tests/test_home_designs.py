"""Alternative homepage design previews at /design/home/<key>."""

import pytest

from app import HOME_DESIGNS


def test_design_index_lists_every_variant(client):
    resp = client.get("/design/home")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'name="robots" content="noindex, nofollow"' in html
    for key in HOME_DESIGNS:
        assert f"/design/home/{key}" in html


@pytest.mark.parametrize("key", sorted(HOME_DESIGNS))
def test_each_design_renders_noindex_with_switcher(client, key):
    resp = client.get(f"/design/home/{key}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'name="robots" content="noindex, nofollow"' in html
    assert 'class="hd-switcher"' in html
    assert 'aria-current="page"' in html


def test_unknown_design_is_404(client):
    assert client.get("/design/home/zz").status_code == 404


def test_live_homepage_is_unchanged_and_not_noindex(client):
    html = client.get("/").get_data(as_text=True)
    assert "hd-switcher" not in html
    assert "noindex" not in html


def test_designs_are_not_in_sitemap(client):
    assert "/design/home" not in client.get("/sitemap.xml").get_data(as_text=True)
