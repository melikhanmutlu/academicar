"""Viewer: three cross-section panels, tap-to-recenter keeps the zoom, AR launch feedback, layer rename."""
from tests.plan_helpers import set_plan_features
from tests.test_scenes import _make_model


def _html(client, mid):
    return client.get(f"/view/{mid}").get_data(as_text=True)


def test_slice_view_shows_one_panel_per_axis_in_a_dock(app, client):
    mid, _ = _make_model(app, plan="academic", email="panels@example.com")
    html = _html(client, mid)
    assert 'class="slice-dock"' in html and 'class="slice-slot"' in html  # desktop dock, phone sheet slot
    for axis in ("x", "y", "z"):
        assert f'data-slice-panel="{axis}"' in html, axis
    assert html.count("<canvas data-slice-canvas") == 3
    assert "const crosshair" in html and "const clickSlice" in html

    set_plan_features(app, "free", remove={"slice_view"})
    free_id, _ = _make_model(app, plan="free", email="panelsfree@example.com")
    free_html = _html(client, free_id)
    for marker in ('class="slice-dock"', 'class="slice-slot"', 'data-slice-panel="x"'):
        assert marker not in free_html, marker


def test_tap_to_recenter_keeps_the_zoom(app, client):
    mid, _ = _make_model(app, plan="academic", email="tap@example.com")
    assert "const keepZoomAfterTap" in _html(client, mid)


def test_ar_button_shows_launch_feedback(app, client):
    mid, _ = _make_model(app, plan="academic", email="arfeedback@example.com")
    html = _html(client, mid)
    assert 'id="arLaunch"' in html and "const showArLaunch" in html and "const hideArLaunch" in html


def test_layers_panel_can_rename_layers(app, client):
    mid, _ = _make_model(app, plan="academic", email="rename@example.com")
    html = _html(client, mid)
    assert "layer-rename" in html and "const pendingNames" in html
