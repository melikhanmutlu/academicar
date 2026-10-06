"""Admins download users' model files from the admin panel; every download is audited."""
import os

from models import AuditLog, Model3D, db
from tests.conftest import register
from tests.test_admin_model_preview import _login_admin, _private_model


def test_admin_downloads_glb_and_source_with_audit(client, app):
    register(client)
    model_id, _ = _private_model(app)
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        source_dir = os.path.join(app.config["UPLOAD_FOLDER"], model_id, "v1")
        os.makedirs(source_dir, exist_ok=True)
        source = os.path.join(source_dir, "scan.stl")
        with open(source, "wb") as fh:
            fh.write(b"solid x")
        model.current_source_path = source
        model.display_name = "Femur model"
        db.session.commit()
    _login_admin(client)

    page = client.get(f"/admin/models/{model_id}").get_data(as_text=True)
    assert f"/admin/models/{model_id}/download/glb" in page
    assert f"/admin/models/{model_id}/download/source" in page

    glb = client.get(f"/admin/models/{model_id}/download/glb")
    assert glb.status_code == 200
    assert glb.data == b"glTF"
    assert "attachment" in glb.headers["Content-Disposition"]
    assert "Femur_model.glb" in glb.headers["Content-Disposition"]
    src = client.get(f"/admin/models/{model_id}/download/source")
    assert src.status_code == 200 and src.data == b"solid x"
    assert client.get(f"/admin/models/{model_id}/download/usdz").status_code == 404  # never generated
    with app.app_context():
        kinds = [log.details for log in AuditLog.query.filter_by(event_type="admin_model_downloaded").all()]
    assert len(kinds) == 2


def test_source_outside_the_upload_folder_is_refused(client, app):
    register(client)
    model_id, _ = _private_model(app)
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        model.current_source_path = os.path.abspath(__file__)
        db.session.commit()
    _login_admin(client)
    assert client.get(f"/admin/models/{model_id}/download/source").status_code == 404


def test_non_admin_cannot_download(client, app):
    register(client)
    model_id, _ = _private_model(app)
    assert client.get(f"/admin/models/{model_id}/download/glb").status_code in (403, 404)


def test_content_policy_page_is_linked(client):
    page = client.get("/content-policy")
    assert page.status_code == 200
    assert "we do not use it" in page.get_data(as_text=True)
    assert "/content-policy" in client.get("/terms").get_data(as_text=True)
