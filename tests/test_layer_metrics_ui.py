"""Per-layer measurements UI: who sees them in the viewer, the model_edit card, the
CSV export and the visibility toggle."""
import csv
import io
import re
from datetime import UTC, datetime

import pytest

from models import Model3D, db
from tests.conftest import create_user, login
from tests.test_layer_editor import PASSWORD, _layers, _make_model as _make_layered


def _metrics(names, truncated=False):
    metrics = {
        "version": 1,
        "unit": "mm",
        "layers": {
            name: {"dims_mm": [30.0 - i, 20.0, 10.0], "max_diameter_mm": 33.3 + i, "centroid": [i * 0.01, 0.0, 0.0]}
            for i, name in enumerate(names)
        },
        "pairs": [
            {"a": names[0], "b": names[1], "min_distance_mm": 3.2, "point_a": [0.001, 0.0, 0.0], "point_b": [0.004, 0.0, 0.0]},
            {"a": names[0], "b": names[2], "min_distance_mm": 1.5, "point_a": [0.001, 0.0, 0.0], "point_b": [0.002, 0.0, 0.0]},
        ],
    }
    if truncated:
        metrics["truncated"] = True
    return metrics


def _setup(app, *, plan="academic", public=False, source_format="glb", names=None, metrics=True, truncated=False):
    mid = _make_layered(app)
    with app.app_context():
        model = db.session.get(Model3D, mid)
        info = dict(model.layer_info)
        if names:
            info["layers"] = [dict(layer, name=names[i]) for i, layer in enumerate(info["layers"])]
        if metrics:
            info["metrics"] = _metrics([layer["name"] for layer in info["layers"]], truncated)
            info["metrics_public"] = public
        model.layer_info = info
        model.license_type = plan
        model.source_format = source_format
        db.session.commit()
    return mid


def _viewer(client, mid):
    return client.get(f"/view/{mid}").get_data(as_text=True)


def _embedded_metrics(html):
    match = re.search(r"const layerMetrics = (.*?);\n", html)
    assert match
    return match.group(1)


def test_viewer_shows_measurements_to_anonymous_when_public_on_paid_plan(client, app):
    mid = _setup(app, public=True)
    html = _viewer(client, mid)
    assert "max_diameter_mm" in _embedded_metrics(html)
    assert 'class="layers-dist"' in html and 'slot="hotspot-dist-a"' in html and 'id="distLine"' in html
    assert "centroid" not in html and "dims_mm" not in _embedded_metrics(html)


def test_viewer_omits_measurements_on_free_plan_even_when_public(client, app):
    mid = _setup(app, plan="free", public=True)
    html = _viewer(client, mid)
    assert _embedded_metrics(html) == "null"
    assert 'class="layers-dist"' not in html and 'slot="hotspot-dist-a"' not in html


def test_viewer_hides_private_measurements_from_anonymous_but_not_editors(client, app):
    mid = _setup(app, public=False)
    assert _embedded_metrics(_viewer(client, mid)) == "null"
    assert 'class="layers-dist"' not in _viewer(client, mid)
    login(client, email="owner@example.com", password=PASSWORD)
    html = _viewer(client, mid)
    assert "max_diameter_mm" in _embedded_metrics(html) and 'class="layers-dist"' in html


def test_viewer_shows_private_measurements_to_a_project_editor(client, app):
    from models import ProjectCollaborator

    mid = _setup(app, public=False)
    with app.app_context():
        editor = create_user(email="editor@example.com", username="Editor")
        db.session.commit()
        paper_id = db.session.get(Model3D, mid).paper_id
        db.session.add(ProjectCollaborator(paper_id=paper_id, user_id=editor.id, email=editor.email, accepted_at=datetime.now(UTC)))
        db.session.commit()
    login(client, email="editor@example.com", password=PASSWORD)
    assert "max_diameter_mm" in _embedded_metrics(_viewer(client, mid))


def test_viewer_without_stored_metrics_has_no_distance_panel(client, app):
    mid = _setup(app, metrics=False)
    login(client, email="owner@example.com", password=PASSWORD)
    html = _viewer(client, mid)
    assert _embedded_metrics(html) == "null" and 'class="layers-dist"' not in html


def test_viewer_notes_for_segmentation_models_and_truncated_metrics(client, app):
    mid = _setup(app, public=True, source_format="segmentation", truncated=True)
    html = _viewer(client, mid)
    assert "Approximate measurements for education, not for diagnosis." in html
    assert "Some distances were not measured." in html
    plain = _setup(app, public=True)
    assert "Approximate measurements for education" not in _viewer(client, plain)
    assert "Some distances were not measured." not in _viewer(client, plain)


def test_viewer_note_absent_for_medical_models_when_measurements_hidden(client, app):
    mid = _setup(app, public=False, source_format="segmentation")
    assert "Approximate measurements for education" not in _viewer(client, mid)


def test_embed_viewer_follows_the_same_rules(client, app):
    mid = _setup(app, public=False)
    assert _embedded_metrics(client.get(f"/view/{mid}?embed=true").get_data(as_text=True)) == "null"


# --- CSV ----------------------------------------------------------------------------------------


@pytest.fixture()
def owner(client, app):
    mid = _setup(app, names=["=cmd|' /C calc'!A0", "Liver, left", "+Spleen"], public=False)
    login(client, email="owner@example.com", password=PASSWORD)
    return mid


def test_csv_has_layer_and_pair_rows_and_neutralises_formulas(client, app, owner):
    resp = client.get(f"/models/{owner}/layer-metrics.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv" and "attachment" in resp.headers["Content-Disposition"]
    body = resp.get_data(as_text=True)
    assert body.startswith("\ufeff")  # Excel needs the BOM for non-ASCII names
    rows = list(csv.reader(io.StringIO(body[1:])))
    assert rows[0][:3] == ["kind", "layer", "layer_b"]
    kinds = [r[0] for r in rows[1:]]
    assert kinds == ["layer"] * 3 + ["pair"] * 2
    layer_rows = {r[1]: r for r in rows[1:] if r[0] == "layer"}
    assert "'=cmd|' /C calc'!A0" in layer_rows and "'+Spleen" in layer_rows and "Liver, left" in layer_rows
    liver = layer_rows["Liver, left"]
    assert liver[3:6] == ["29.0", "20.0", "10.0"] and liver[6] == "34.3"
    pairs = [r for r in rows[1:] if r[0] == "pair"]
    assert [p[8] for p in pairs] == ["1.5", "3.2"]  # shortest first
    assert pairs[0][1] == "'=cmd|' /C calc'!A0" and pairs[0][2] == "'+Spleen"
    assert not any(cell[:1] in "=+-@" and cell for row in rows for cell in row)


def test_csv_is_editor_only_and_paid_only(client, app, owner):
    client.post("/auth/logout")
    assert client.get(f"/models/{owner}/layer-metrics.csv").status_code in (302, 401)
    with app.app_context():
        create_user(email="stranger@example.com", username="Stranger")
        db.session.commit()
    login(client, email="stranger@example.com", password=PASSWORD)
    assert client.get(f"/models/{owner}/layer-metrics.csv").status_code in (403, 404)
    client.post("/auth/logout")
    login(client, email="owner@example.com", password=PASSWORD)
    with app.app_context():
        model = db.session.get(Model3D, owner)
        model.license_type = "free"
        db.session.commit()
    assert client.get(f"/models/{owner}/layer-metrics.csv").status_code == 403


def test_csv_404_without_metrics(client, app):
    mid = _setup(app, metrics=False)
    login(client, email="owner@example.com", password=PASSWORD)
    assert client.get(f"/models/{mid}/layer-metrics.csv").status_code == 404


# --- visibility toggle --------------------------------------------------------------------------


def _public(app, mid):
    with app.app_context():
        return db.session.get(Model3D, mid).layer_info["metrics_public"]


def test_visibility_toggle_round_trip_keeps_metrics(client, app, owner):
    resp = client.post(f"/models/{owner}/layer-metrics/visibility", data={"metrics_public": "1"})
    assert resp.status_code == 302 and _public(app, owner) is True
    with app.app_context():
        assert db.session.get(Model3D, owner).layer_info["metrics"]["pairs"]
    client.post(f"/models/{owner}/layer-metrics/visibility", data={"metrics_public": "0"})
    assert _public(app, owner) is False


def test_visibility_toggle_is_editor_only(client, app, owner):
    client.post("/auth/logout")
    client.post(f"/models/{owner}/layer-metrics/visibility", data={"metrics_public": "1"})
    assert _public(app, owner) is False
    with app.app_context():
        create_user(email="stranger@example.com", username="Stranger")
        db.session.commit()
    login(client, email="stranger@example.com", password=PASSWORD)
    resp = client.post(f"/models/{owner}/layer-metrics/visibility", data={"metrics_public": "1"})
    assert resp.status_code in (403, 404) and _public(app, owner) is False


def test_visibility_cannot_be_turned_on_without_the_plan_but_can_be_turned_off(client, app, owner):
    with app.app_context():
        model = db.session.get(Model3D, owner)
        info = dict(model.layer_info)
        info["metrics_public"] = True
        model.layer_info = info
        model.license_type = "free"
        db.session.commit()
    client.post(f"/models/{owner}/layer-metrics/visibility", data={"metrics_public": "0"})
    assert _public(app, owner) is False
    client.post(f"/models/{owner}/layer-metrics/visibility", data={"metrics_public": "1"})
    assert _public(app, owner) is False


def test_visibility_form_carries_a_csrf_token_and_posts_need_it(client, app, owner):
    edit = client.get(f"/models/{owner}/edit").get_data(as_text=True)
    form = re.search(r'<form method="POST" action="/models/[^"]+/layer-metrics/visibility">(.*?)</form>', edit, re.S)
    assert form and 'name="csrf_token"' in form.group(1) and 'name="metrics_public" value="1"' in form.group(1)
    app.config["WTF_CSRF_ENABLED"] = True
    client.post(f"/models/{owner}/layer-metrics/visibility", data={"metrics_public": "1"})
    assert _public(app, owner) is False


# --- model_edit card ----------------------------------------------------------------------------


def test_edit_card_lists_layers_pairs_csv_link_and_state(client, app, owner):
    login(client, email="owner@example.com", password=PASSWORD)
    html = client.get(f"/models/{owner}/edit").get_data(as_text=True)
    assert 'id="measurementsSection"' in html and "Measurements</h2>" in html
    assert "29.0 × 20.0 × 10.0" in html and "34.3" in html
    assert html.index("1.5</td>") < html.index("3.2</td>")  # shortest first
    assert f"/models/{owner}/layer-metrics.csv" in html and "Download CSV" in html
    assert "Show measurements to viewers: <strong>Off</strong>" in html
    client.post(f"/models/{owner}/layer-metrics/visibility", data={"metrics_public": "1"})
    assert "Show measurements to viewers: <strong>On</strong>" in client.get(f"/models/{owner}/edit").get_data(as_text=True)


def test_edit_card_shows_the_volume_column_only_when_layers_have_volumes(client, app, owner):
    login(client, email="owner@example.com", password=PASSWORD)
    assert "Volume (mL)" not in client.get(f"/models/{owner}/edit").get_data(as_text=True)
    with app.app_context():
        model = db.session.get(Model3D, owner)
        info = dict(model.layer_info)
        info["layers"] = [dict(layer, volume_ml=12.34) for layer in info["layers"]]
        model.layer_info = info
        db.session.commit()
    html = client.get(f"/models/{owner}/edit").get_data(as_text=True)
    assert "Volume (mL)" in html and "12.3" in html
    csv_rows = list(csv.reader(io.StringIO(client.get(f"/models/{owner}/layer-metrics.csv").get_data(as_text=True))))
    assert csv_rows[1][7] == "12.34"


def test_edit_card_says_measurements_are_being_prepared_when_missing(client, app):
    mid = _setup(app, metrics=False)
    login(client, email="owner@example.com", password=PASSWORD)
    html = client.get(f"/models/{mid}/edit").get_data(as_text=True)
    assert "Measurements are being prepared" in html and "Download CSV" not in html


def test_edit_card_shows_the_paid_plan_note_on_free_models(client, app):
    mid = _setup(app, plan="free", public=True)
    login(client, email="owner@example.com", password=PASSWORD)
    html = client.get(f"/models/{mid}/edit").get_data(as_text=True)
    assert "available on paid plans" in html and "Download CSV" not in html
    assert "Measurements are being prepared" not in html and "29.0 × 20.0" not in html


def test_edit_card_absent_for_models_without_layers(client, app):
    mid = _make_layered(app, layered=False)
    login(client, email="owner@example.com", password=PASSWORD)
    assert 'id="measurementsSection"' not in client.get(f"/models/{mid}/edit").get_data(as_text=True)


def test_layer_rename_keeps_measurements_in_the_card(client, app, owner):
    layers = _layers(app, owner)
    data = {f"layer_name_{i}": layer["name"] for i, layer in enumerate(layers)}
    data["layer_name_1"] = "Renamed"
    for i, layer in enumerate(layers):
        if layer.get("color"):
            data[f"layer_color_{i}"] = layer["color"]
    login(client, email="owner@example.com", password=PASSWORD)
    client.post(f"/models/{owner}/layers", data=data)
    html = client.get(f"/models/{owner}/edit").get_data(as_text=True)
    assert "<td>Renamed</td>" in html
