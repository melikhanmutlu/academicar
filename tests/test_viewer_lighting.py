"""Viewer lighting presets: defaults, saving by editors, and the viewer markup."""
from models import Model3D, db
from tests.test_scenes import _login, _make_model
from viewer_lighting import LIGHTING_PRESETS, clean_lighting, model_lighting


def test_default_is_studio_with_ground_shadow(app):
    mid, _ = _make_model(app)
    with app.app_context():
        lighting = model_lighting(db.session.get(Model3D, mid))
    assert lighting["preset"] == "studio" and lighting["environment"] == "studio"
    assert lighting["shadow_intensity"] > 0


def test_clean_lighting_validates_and_clamps():
    assert clean_lighting({"preset": "soft", "exposure": 9, "shadow_softness": -1}) == {
        "preset": "soft", "exposure": 3.0, "shadow_softness": 0.0,
    }
    for bad in (None, {"preset": "disco"}, {"preset": "soft", "exposure": "x"}):
        try:
            clean_lighting(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_owner_saves_lighting_and_viewer_opens_with_it(app, client):
    mid, _ = _make_model(app)
    assert client.post(f"/models/{mid}/lighting", json={"preset": "dim"}).status_code in (302, 401)
    _login(client)
    response = client.post(f"/models/{mid}/lighting", json={"preset": "dramatic", "exposure": 0.9})
    assert response.status_code == 200 and response.get_json()["lighting"]["exposure"] == 0.9
    html = client.get(f"/view/{mid}").get_data(as_text=True)
    assert 'exposure="0.9"' in html and 'environment-image="neutral"' in html
    assert f'shadow-intensity="{LIGHTING_PRESETS["dramatic"]["shadow_intensity"]}"' in html
    assert "<button type=\"button\" data-light-save>" in html
    assert client.post(f"/models/{mid}/lighting", json={"preset": "nope"}).status_code == 400


def test_other_users_cannot_save_lighting(app, client):
    mid, _ = _make_model(app)
    _make_model(app, email="other@example.com")
    _login(client, "other@example.com")
    assert client.post(f"/models/{mid}/lighting", json={"preset": "dim"}).status_code in (403, 404)
    assert "<button type=\"button\" data-light-save>" not in client.get(f"/view/{mid}").get_data(as_text=True)
