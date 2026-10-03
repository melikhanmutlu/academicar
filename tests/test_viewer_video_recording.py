"""Viewer video recording (paid plans). A browser run found every download
empty: the code recorded model-viewer's hidden canvas. These checks keep the
template wired to the visible canvas, a playable format order, an opaque
backdrop and a WebM duration fix."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWER = (ROOT / "templates" / "viewer.html").read_text()


def test_recording_uses_the_visible_canvas():
    assert "classList.contains('show')" in VIEWER
    start = VIEWER.index("const startVideoRecording")
    body = VIEWER[start:VIEWER.index("videoRecordButton?.addEventListener('click'", start)]
    assert "querySelector('canvas')" not in body
    assert "visibleViewerCanvas()" in body


def test_h264_is_preferred_and_bare_mp4_is_last():
    start = VIEWER.index("const preferredTypes = [")
    types = VIEWER[start:VIEWER.index("];", start)]
    assert types.index("avc1") < types.index("video/webm")
    assert types.rstrip().rstrip(",").endswith("'video/mp4'")


def test_frames_get_the_viewer_backdrop_and_full_resolution():
    assert "paintVideoBackdrop(frameCtx" in VIEWER
    assert "minimumRenderScale = 1" in VIEWER
    assert "minimumRenderScale = previousRenderScale" in VIEWER


def test_webm_duration_fix_is_vendored_and_loaded():
    assert (ROOT / "static" / "vendor" / "fix-webm-duration.js").exists()
    assert (ROOT / "static" / "vendor" / "fix-webm-duration.LICENSE").exists()
    assert "vendor/fix-webm-duration.js" in VIEWER
    assert "ysFixWebmDuration(blob" in VIEWER
