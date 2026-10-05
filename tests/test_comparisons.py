"""Before / after comparison: two models side by side behind one public link."""
import uuid
from datetime import UTC, datetime, timedelta

from models import Model3D, ModelComparison, Paper, ProjectCollaborator, User, db
from tests.conftest import create_user, login

OWNER = "owner@example.com"
EDITOR = "editor@example.com"
STRANGER = "stranger@example.com"


def _user(email):
    with_row = User.query.filter_by(email=email).first()
    return with_row or create_user(email=email, username=email.split("@")[0])


def _model(app, email=OWNER, *, plan="academic", status="ready", visibility="public", name="Model", paper_id=None,
           layers=None):
    with app.app_context():
        user = _user(email)
        if paper_id is None:
            paper = Paper(title=f"P {name}", slug=f"p-{uuid.uuid4().hex[:8]}", user_id=user.id,
                          visibility=visibility, is_public=visibility == "public")
            db.session.add(paper)
            db.session.flush()
            paper_id = paper.id
        model = Model3D(
            id=str(uuid.uuid4()), paper_id=paper_id, user_id=user.id, display_name=name,
            glb_path="converted/x/model.glb", license_type=plan, processing_status=status,
            access_starts_at=datetime.now(UTC), access_expires_at=datetime.now(UTC) + timedelta(days=365),
            layer_info={"layers": layers} if layers else None,
        )
        db.session.add(model)
        db.session.commit()
        return model.id, paper_id


def _create(client, left, right, **extra):
    data = {"left_model_id": left, "right_model_id": right, "title": "Femur", "left_label": "Before",
            "right_label": "After", "sync_camera": "on"}
    data.update(extra)
    return client.post("/comparisons", data=data)


def _comparison(app, left, right, email=OWNER, **kwargs):
    with app.app_context():
        row = ModelComparison(public_id=uuid.uuid4().hex[:16], owner_user_id=_user(email).id, title="Femur",
                              left_model_id=left, right_model_id=right, left_label="Before",
                              right_label="After", sync_camera=True, **kwargs)
        db.session.add(row)
        db.session.commit()
        return row.id, row.public_id


def _count(app):
    with app.app_context():
        return ModelComparison.query.count()


def test_owner_creates_a_comparison(client, app):
    left, _ = _model(app, name="Pre-op")
    right, _ = _model(app, name="Post-op")
    login(client, email=OWNER)
    page = client.get(f"/comparisons/new?left={left}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Post-op" in html and 'name="csrf_token"' in html and 'value="Before"' in html

    response = _create(client, left, right)
    assert response.status_code == 302 and f"/models/{left}/edit" in response.headers["Location"]
    with app.app_context():
        row = ModelComparison.query.one()
        assert (row.left_model_id, row.right_model_id) == (left, right)
        assert row.title == "Femur" and row.sync_camera is True and row.owner_user_id == _user(OWNER).id


def test_blank_labels_default_and_sync_can_be_off(client, app):
    left, _ = _model(app)
    right, _ = _model(app)
    login(client, email=OWNER)
    client.post("/comparisons", data={"left_model_id": left, "right_model_id": right, "title": "",
                                      "left_label": "", "right_label": ""})
    with app.app_context():
        row = ModelComparison.query.one()
        assert (row.left_label, row.right_label, row.sync_camera) == ("Before", "After", False)
        assert row.title


def test_collaborator_can_create_across_projects_they_edit(client, app):
    left, left_paper = _model(app, OWNER, name="Left")
    with app.app_context():
        editor = _user(EDITOR)
        db.session.add(ProjectCollaborator(paper_id=left_paper, user_id=editor.id, email=EDITOR,
                                           accepted_at=datetime.now(UTC)))
        db.session.commit()
    right, _ = _model(app, EDITOR, name="Mine")
    login(client, email=EDITOR)
    html = client.get(f"/comparisons/new?left={left}").get_data(as_text=True)
    assert "Mine" in html
    assert _create(client, left, right).status_code == 302
    assert _count(app) == 1


def test_stranger_models_are_refused(client, app):
    mine, _ = _model(app, OWNER)
    theirs, _ = _model(app, STRANGER)
    login(client, email=OWNER)
    response = _create(client, mine, theirs)
    assert response.status_code == 302 and "/comparisons/new" in response.headers["Location"]
    assert _create(client, theirs, mine).status_code == 404
    assert client.get(f"/comparisons/new?left={theirs}").status_code == 404
    assert _count(app) == 0
    # The picker only lists models the user can edit (the stranger's is named "Model" too).
    with app.app_context():
        db.session.get(Model3D, theirs).display_name = "Strangers secret"
        db.session.commit()
    assert "Strangers secret" not in client.get(f"/comparisons/new?left={mine}").get_data(as_text=True)


def test_same_model_twice_is_refused(client, app):
    left, _ = _model(app)
    login(client, email=OWNER)
    _create(client, left, left)
    assert _count(app) == 0


def test_unready_model_is_refused(client, app):
    left, _ = _model(app)
    right, _ = _model(app, status="processing")
    login(client, email=OWNER)
    _create(client, left, right)
    assert _count(app) == 0
    _create(client, right, left)
    assert _count(app) == 0
    assert "Processing" not in client.get(f"/comparisons/new?left={left}").get_data(as_text=True)


def test_free_plan_left_model_is_refused(client, app):
    left, _ = _model(app, plan="free")
    right, _ = _model(app, plan="academic")
    login(client, email=OWNER)
    response = client.get(f"/comparisons/new?left={left}")
    assert response.status_code == 302 and f"/models/{left}/edit" in response.headers["Location"]
    _create(client, left, right)
    assert _count(app) == 0
    # A free model on the right is fine when the left (primary) model's plan allows it.
    free_right, _ = _model(app, plan="free")
    _create(client, right, free_right)
    assert _count(app) == 1


def test_title_and_label_limits(client, app):
    left, _ = _model(app)
    right, _ = _model(app)
    login(client, email=OWNER)
    _create(client, left, right, title="x" * 121)
    _create(client, left, right, left_label="y" * 61)
    assert _count(app) == 0
    _create(client, left, right, title="x" * 120, left_label="y" * 60)
    assert _count(app) == 1


def test_creating_requires_login(client, app):
    left, _ = _model(app)
    right, _ = _model(app)
    assert _create(client, left, right).status_code == 302
    assert client.get(f"/comparisons/new?left={left}").status_code == 302
    assert _count(app) == 0


def test_public_page_renders_both_halves(client, app):
    left, _ = _model(app, name="Pre-op")
    right, _ = _model(app, name="Post-op")
    _, public_id = _comparison(app, left, right)
    response = client.get(f"/c/{public_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.count("<model-viewer") == 2
    assert f"/files/{left}/model.glb" in html and f"/files/{right}/model.glb" in html
    assert "Before" in html and "After" in html and "Sync cameras" in html and "Reset view" in html
    assert f"/view/{left}" in html and f"/view/{right}" in html
    assert "This model is not available" not in html


def test_ar_follows_each_models_plan(client, app, monkeypatch):
    left, _ = _model(app, plan="academic")
    right, _ = _model(app, plan="free")
    _, public_id = _comparison(app, left, right)
    assert client.get(f"/c/{public_id}").get_data(as_text=True).count("ar-modes=") == 2

    import comparisons
    real = comparisons.get_license_plan

    def no_ar_for_free(license_type):
        plan = real(license_type)
        if license_type != "free":
            return plan
        return type("Plan", (), {"features": plan.features - {"ar"}})()

    monkeypatch.setattr(comparisons, "get_license_plan", no_ar_for_free)
    assert client.get(f"/c/{public_id}").get_data(as_text=True).count("ar-modes=") == 1


def test_layer_list_for_layered_models(client, app):
    layers = [{"name": "Femur", "color": "#aa3322", "materials": ["m1"]},
              {"name": "Tibia", "color": "#2233aa", "materials": ["m2"]}]
    left, _ = _model(app, layers=layers)
    right, _ = _model(app)
    _, public_id = _comparison(app, left, right)
    html = client.get(f"/c/{public_id}").get_data(as_text=True)
    assert html.count('data-layers="') == 1 and "Femur" in html and "Tibia" in html and "#aa3322" in html


def test_private_half_shows_a_placeholder(client, app):
    left, _ = _model(app)
    right, _ = _model(app, visibility="private", name="Secret post-op")
    _, public_id = _comparison(app, left, right)
    response = client.get(f"/c/{public_id}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.count("<model-viewer") == 1 and "This model is not available" in html
    assert "Secret post-op" not in html and f"/files/{right}/" not in html and "Sync cameras" not in html


def test_expired_half_shows_a_placeholder(client, app):
    left, _ = _model(app)
    right, _ = _model(app)
    with app.app_context():
        db.session.get(Model3D, right).access_expires_at = datetime.now(UTC) - timedelta(days=1)
        db.session.commit()
    _, public_id = _comparison(app, left, right)
    response = client.get(f"/c/{public_id}")
    assert response.status_code == 200
    assert "This model is not available" in response.get_data(as_text=True)


def test_both_halves_private_is_403(client, app):
    left, _ = _model(app, visibility="private")
    right, _ = _model(app, visibility="private")
    _, public_id = _comparison(app, left, right)
    response = client.get(f"/c/{public_id}")
    assert response.status_code == 403 and "<model-viewer" not in response.get_data(as_text=True)
    login(client, email=OWNER)
    assert client.get(f"/c/{public_id}").status_code == 200


def test_both_halves_expired_is_410(client, app):
    left, _ = _model(app)
    right, _ = _model(app)
    with app.app_context():
        for mid in (left, right):
            db.session.get(Model3D, mid).access_expires_at = datetime.now(UTC) - timedelta(days=1)
        db.session.commit()
    _, public_id = _comparison(app, left, right)
    response = client.get(f"/c/{public_id}")
    assert response.status_code == 410 and "<model-viewer" not in response.get_data(as_text=True)


def test_feature_gone_from_both_plans_is_410(client, app):
    left, _ = _model(app, plan="free")
    right, _ = _model(app, plan="free")
    _, public_id = _comparison(app, left, right)
    assert client.get(f"/c/{public_id}").status_code == 410


def test_unknown_public_id_is_404(client):
    assert client.get("/c/nope").status_code == 404


def test_owner_deletes_a_comparison(client, app):
    left, _ = _model(app)
    right, _ = _model(app)
    cid, public_id = _comparison(app, left, right)
    assert client.post(f"/comparisons/{cid}/delete").status_code == 302
    assert _count(app) == 1  # anonymous visitors cannot delete
    with app.app_context():
        _user(STRANGER)
    client.post("/auth/logout")
    login(client, email=STRANGER)
    assert client.post(f"/comparisons/{cid}/delete").status_code == 403
    client.post("/auth/logout")
    login(client, email=OWNER)
    response = client.post(f"/comparisons/{cid}/delete")
    assert response.status_code == 302 and f"/models/{left}/edit" in response.headers["Location"]
    assert _count(app) == 0
    assert client.get(f"/c/{public_id}").status_code == 404


def test_deleting_a_model_removes_its_comparisons(client, app):
    left, _ = _model(app)
    right, _ = _model(app)
    _comparison(app, left, right)
    with app.app_context():
        db.session.delete(db.session.get(Model3D, right))
        db.session.commit()
        assert ModelComparison.query.count() == 0
        assert db.session.get(Model3D, left) is not None


def test_qr_assets(client, app):
    left, _ = _model(app)
    right, _ = _model(app)
    cid, public_id = _comparison(app, left, right)
    assert client.get(f"/qr-print/comparison/{cid}/label.svg").status_code == 302  # login required
    login(client, email=OWNER)
    response = client.get(f"/qr-print/comparison/{cid}/label.svg")
    assert response.status_code == 200 and response.mimetype == "image/svg+xml"
    assert f"qr-compare-{public_id}-label.svg" in response.headers["Content-Disposition"]
    assert client.get(f"/qr-print/comparison/{cid}/qr.svg").status_code == 200
    assert client.get(f"/qr-print/comparison/{cid}/bogus.exe").status_code == 404
    client.post("/auth/logout")
    with app.app_context():
        _user(STRANGER)
    login(client, email=STRANGER)
    assert client.get(f"/qr-print/comparison/{cid}/label.svg").status_code == 403


def test_model_edit_compare_section_by_plan(client, app):
    paid, _ = _model(app, plan="academic")
    free, _ = _model(app, plan="free")
    right, _ = _model(app)
    cid, public_id = _comparison(app, paid, right)
    login(client, email=OWNER)

    html = client.get(f"/models/{paid}/edit").get_data(as_text=True)
    assert 'id="compareSection"' in html and f"/comparisons/new?left={paid}" in html
    assert f"/c/{public_id}" in html and f"/comparisons/{cid}/delete" in html
    assert f"/qr-print/comparison/{cid}/label.png" in html

    html = client.get(f"/models/{free}/edit").get_data(as_text=True)
    assert "Before / after comparison is available on paid plans." in html
    assert f"/comparisons/new?left={free}" not in html
