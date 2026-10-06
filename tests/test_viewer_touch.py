"""Phones: the full-screen viewer takes every drag (vertical drags tilt the model); embeds keep pan-y."""
from tests.test_scenes import _make_model


def test_full_viewer_orbits_on_every_drag_and_embeds_let_the_page_scroll(app, client):
    mid, _ = _make_model(app)
    assert 'touch-action="none"' in client.get(f"/view/{mid}").get_data(as_text=True)
    assert 'touch-action="pan-y"' in client.get(f"/view/{mid}?embed=true").get_data(as_text=True)
