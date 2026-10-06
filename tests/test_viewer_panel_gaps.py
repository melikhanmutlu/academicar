"""Viewer panel follow-ups: embed tools, figure export without screenshots, embed background."""
from tests.plan_helpers import set_plan_features
from tests.test_scenes import _make_model


def _html(client, mid, query=""):
    return client.get(f"/view/{mid}{query}").get_data(as_text=True)


def test_embed_has_measure(app, client):
    mid, _ = _make_model(app)
    html = _html(client, mid, "?embed=true")
    assert 'id="measureBtn"' in html and 'id="layersBtn"' in html


def test_figure_export_without_the_screenshot_feature(app, client):
    mid, _ = _make_model(app, plan="academic")
    set_plan_features(app, "academic", remove={"screenshots"})
    html = _html(client, mid)
    assert "data-screenshot-tools" not in html.split("<script")[0]
    assert '<button type="button" data-export-figure disabled>Export figure</button>' in html


def test_embed_background_parameter_is_read_before_paint(app, client):
    mid, _ = _make_model(app)
    assert "get('bg')" in _html(client, mid, "?embed=true&bg=white")


def test_saved_media_does_not_fetch_data_urls():
    # The CSP's connect-src blocks fetch(data:), which broke "Current view"
    # and "Generate 8 views" saving; data URLs are decoded with atob instead.
    with open("templates/viewer.html", encoding="utf-8") as fh:
        source = fh.read()
    block = source.split("const dataUrlToBlob", 1)[1].split("window.viewerMedia", 1)[0]
    assert "fetch(" not in block and "atob(" in block
