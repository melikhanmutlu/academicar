"""The landing page's showreel must not cost page-load bytes until played."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_landing_showreel_is_click_to_load(client):
    body = client.get("/").get_data(as_text=True)
    video = re.search(r"<video[^>]*lp-launch-video[^>]*>", body).group(0)
    assert 'preload="none"' in video
    assert "autoplay" not in video
    assert "poster=" not in video  # the poster is a lazy <img> instead
    assert re.search(r'<img[^>]*showreel-poster\.webp[^>]*loading="lazy"', body)
    assert "/static/videos/academicar-showreel.mp4" in body


def test_showreel_assets_are_web_sized():
    video = STATIC / "videos" / "academicar-showreel.mp4"
    poster = STATIC / "images" / "showreel-poster.webp"
    assert video.stat().st_size < 10 * 1024 * 1024
    assert poster.stat().st_size < 60 * 1024
    # faststart: the moov atom precedes mdat so playback starts before the full download
    head = video.read_bytes()[:64 * 1024]
    assert head.find(b"moov") != -1 and (head.find(b"mdat") == -1 or head.find(b"moov") < head.find(b"mdat"))
