from models import ModelAnnotation, db
from tests.test_admin_enrichment import _make_admin, _seed_annotation, _seed_model


def _page(client, query=""):
    return client.get("/admin/annotations" + query).get_data(as_text=True)


def test_search_matches_label_and_description(client):
    _make_admin(client)
    model_id = _seed_model(client)
    _seed_annotation(client, model_id, label="Spam hotspot")
    _seed_annotation(client, model_id, label="Femur", description="contains BUYNOW link")
    _seed_annotation(client, model_id, label="Clean one")
    text = _page(client, "?annotation_q=spam")
    assert "Spam hotspot" in text and "Clean one" not in text and "Femur" not in text
    text = _page(client, "?annotation_q=buynow")
    assert "Femur" in text and "Spam hotspot" not in text
    assert "1 annotation<" in text


def test_search_is_literal_for_wildcards(client):
    _make_admin(client)
    model_id = _seed_model(client)
    _seed_annotation(client, model_id, label="100% bone")
    _seed_annotation(client, model_id, label="Other")
    text = _page(client, "?annotation_q=%25")
    assert "100% bone" in text and "Other" not in text


def test_filtered_empty_state_and_plain_empty_state(client):
    _make_admin(client)
    assert "No annotations yet." in _page(client)
    model_id = _seed_model(client)
    _seed_annotation(client, model_id, label="Spam hotspot")
    assert "No annotations match these filters." in _page(client, "?annotation_q=zzz")


def test_page_has_single_heading_and_named_confirm(client):
    _make_admin(client)
    model_id = _seed_model(client)
    _seed_annotation(client, model_id, label="Spam hotspot")
    text = _page(client)
    assert "Model annotations" not in text
    assert "Every hotspot label" not in text
    assert "Delete annotation" in text and "Spam hotspot" in text
    assert "admin-hide-sm" in text


def test_pagination_keeps_search(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        for i in range(55):
            db.session.add(
                ModelAnnotation(model_id=model_id, position_x=0, position_y=0, position_z=0, label=f"Spam {i}")
            )
        db.session.commit()
    text = _page(client, "?annotation_q=spam")
    assert "annotation_q=spam" in text and "page=2" in text
