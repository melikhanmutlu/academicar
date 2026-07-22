"""Regression guards for viewer tools-panel bugs found in the tools audit.

These assert on viewer.html source (the repo's convention for client-side viewer
JS, see test_faz3/test_faz4): the fixes live in inline JS with no server round
trip, so a source-level guard is what protects them from silent removal.

Bugs fixed:
  #1 Screenshot / "Generate 8 views" drifted when auto-rotate was on.
  #2 Annotation note popover was invisible in full screen.
  #3 Entering annotate mode did not stop auto-rotate (labels landed off-target).
"""
from pathlib import Path

VIEWER = Path(__file__).resolve().parent.parent / "templates" / "viewer.html"


def _viewer_source() -> str:
    return VIEWER.read_text(encoding="utf-8")


def test_screenshot_capture_suspends_auto_rotate():
    """Both the single-view and 8-view capture paths must suspend auto-rotate
    so shots are taken at the exact intended orbit, then restore it."""
    src = _viewer_source()
    # Appears once in downloadCurrentShot and once in generateShotSet.
    assert src.count("const wasAuto = viewer.hasAttribute('auto-rotate')") >= 2
    # Each guarded block restores the prior state.
    assert src.count("if (wasAuto) setAutoRotateState(true)") >= 2
    assert src.count("if (wasAuto) setAutoRotateState(false)") >= 2


def test_annotation_note_popover_reparents_for_fullscreen():
    """The note popover (position:fixed) must be re-parented into the current
    fullscreen element, not left on <body>, or it is invisible in full screen."""
    src = _viewer_source()
    assert "const noteHost = document.fullscreenElement" in src
    assert "noteHost.appendChild(annotationNotePopover)" in src
    # The old unconditional body-append (the bug) must be gone.
    assert "document.body.appendChild(annotationNotePopover)" not in src


def test_entering_annotate_mode_stops_auto_rotate():
    """Precise placement needs a still model: annotate mode must stop auto-rotate.
    setAutoRotateState(false) now appears both in flyCameraToAnnotation and in
    setAnnotateMode's on-branch."""
    src = _viewer_source()
    assert "Stop auto-rotate on entering annotate mode." in src
    assert src.count("if (typeof setAutoRotateState === 'function') setAutoRotateState(false);") >= 2
