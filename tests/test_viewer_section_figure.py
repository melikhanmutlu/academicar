"""Section plane and publication-figure export: paid-plan viewer controls and the scene QR image."""
from tests.test_scenes import _make_model, _scene_public_id


def _html(client, mid, query=""):
    return client.get(f"/view/{mid}{query}").get_data(as_text=True)


def test_section_and_figure_controls_render_only_on_paid_plans(app, client):
    paid_id, _ = _make_model(app, plan="academic")
    html = _html(client, paid_id)
    assert 'id="sectionBtn"' in html and 'id="sectionPanel"' in html
    assert "data-export-figure disabled" in html and 'id="figureModal"' in html
    assert 'id="sectionBtn"' in _html(client, paid_id, "?embed=true")

    free_id, _ = _make_model(app, plan="free", email="free@example.com")
    free_html = _html(client, free_id)
    for marker in ('id="sectionBtn"', 'id="sectionPanel"', "data-export-figure disabled", 'id="figureModal"'):
        assert marker not in free_html, marker
    assert 'id="sectionBtn"' not in _html(client, free_id, "?embed=true")


def test_scene_qr_image_is_the_scene_link_and_scoped_to_its_model(app, client):
    mid, _ = _make_model(app)
    other_id, _ = _make_model(app, email="other@example.com")
    scene_id, _ = _scene_public_id(app, mid)

    response = client.get(f"/qr-image/{mid}?scene={scene_id}")
    assert response.status_code == 200 and response.mimetype == "image/png"
    assert response.data != client.get(f"/qr-image/{mid}").data  # the model's own QR encodes another URL
    # A scene of another model falls back to that model's own QR rather than leaking the scene link.
    assert client.get(f"/qr-image/{other_id}?scene={scene_id}").data == client.get(f"/qr-image/{other_id}").data

    free_id, _ = _make_model(app, plan="free", email="free2@example.com")
    free_scene, _ = _scene_public_id(app, free_id)
    assert client.get(f"/qr-image/{free_id}?scene={free_scene}").data == client.get(f"/qr-image/{free_id}").data


def test_measure_on_slice_ships_with_the_section_panel_only(app, client):
    paid_id, _ = _make_model(app, plan="academic", email="slice@example.com")
    html = _html(client, paid_id)
    assert "data-section-face disabled" in html and "window.viewerSection" in html
    assert "section-change" in html  # Measure clears a slice measurement when the cut moves

    free_id, _ = _make_model(app, plan="free", email="slicefree@example.com")
    free_html = _html(client, free_id)
    assert 'id="measureBtn"' in free_html  # 3D Measure stays on every plan
    assert "data-section-face disabled" not in free_html  # no section panel, so Measure stays 3D
