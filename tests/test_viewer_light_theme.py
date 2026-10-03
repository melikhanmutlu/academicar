"""Viewer Light/White backgrounds: controls styled in viewer.html with white
text must have light-theme colours too, or they vanish on the white panel."""
import re
from pathlib import Path

VIEWER = Path(__file__).resolve().parent.parent / "templates" / "viewer.html"
LIGHT = 'html:is([data-viewer-bg="light"], [data-viewer-bg="white"])'


def test_white_text_controls_have_light_theme_colours():
    css = VIEWER.read_text()
    for selector in (
        ".viewer-publish-cta",
        ".viewer-video-status",
        ".viewer-video-actions button",
        ".viewer-color-swatch",
        ".viewer-color-custom",
    ):
        pattern = re.escape(f"{LIGHT} {selector}") + r"[^{]*\{[^}]*color:\s*#"
        assert re.search(pattern, css), f"no light-theme colour for {selector}"
