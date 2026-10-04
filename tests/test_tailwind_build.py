"""Tailwind is a compiled stylesheet, not the in-browser Play CDN."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_templates_do_not_load_the_tailwind_play_cdn():
    for path in (ROOT / "templates").rglob("*.html"):
        assert "cdn.tailwindcss.com" not in path.read_text(), path


def test_compiled_tailwind_is_linked_last_in_head(client):
    """Last in <head> is where the Play CDN injected its <style>, so utility
    classes keep the same precedence over style.css."""
    html = client.get("/about").get_data(as_text=True)
    head = html.split("</head>", 1)[0]
    assert "css/tailwind.css" in head
    assert head.rindex("css/tailwind.css") > head.rindex("css/style.css")


def test_compiled_tailwind_contains_template_utilities():
    css = (ROOT / "static/css/tailwind.css").read_text()
    for selector in (".min-h-screen", ".font-serif", ".text-academic-900", ".bg-\\[\\#111111\\]\\/95"):
        assert selector in css, selector


def test_csp_drops_the_tailwind_cdn_but_keeps_eval_for_draco_wasm(client):
    policy = client.get("/about").headers.get("Content-Security-Policy", "")
    assert "cdn.tailwindcss.com" not in policy
    # model-viewer's Draco decoder instantiates WebAssembly.
    assert "'unsafe-eval'" in policy
