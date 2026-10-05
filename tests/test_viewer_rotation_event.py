from tests.test_analytics import _public_model


def test_public_viewer_tracks_first_user_rotation(client, app):
    _, model_id = _public_model(app)

    response = client.get(f"/view/{model_id}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "viewer_model_rotated" in html
    assert "camera-change" in html
    assert "'user-interaction'" in html
    assert "viewer_fullscreen_opened" in html
