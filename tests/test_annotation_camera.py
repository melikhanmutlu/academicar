"""A label's saved camera must survive storage: the columns are short
(String(64)/(96)/(16)) while JavaScript prints full float precision."""
import uuid

from models import ModelAnnotation, Model3D, Paper, User, db
from tests.conftest import register


def _model(app):
    with app.app_context():
        user = User.query.filter_by(email="user@example.com").one()
        paper = Paper(title="Cam", slug=f"cam-{uuid.uuid4().hex[:8]}", user_id=user.id)
        db.session.add(paper); db.session.flush()
        model = Model3D(id=str(uuid.uuid4()), paper_id=paper.id, user_id=user.id, glb_path="m.glb",
                        processing_status="ready", license_type="academic")
        db.session.add(model); db.session.commit()
        return model.id


def _post(client, model_id, camera):
    return client.post(f"/models/{model_id}/annotations",
                       json={"label": "Cam", "position": [0, 0, 0], "normal": [0, 1, 0], "camera": camera})


def test_long_default_view_orbit_keeps_its_radius_and_units(client, app):
    register(client)
    model_id = _model(app)
    resp = _post(client, model_id, {
        "orbit": "-2.7755575615628914e-17rad 1.5707963267948966rad 0.4175509772439928m",
        "target": "0.42781234567891234m 0.18751234567891234m -0.30391234567891234m",
        "fov": "18.7689612889979",
    })
    assert resp.status_code == 201
    with app.app_context():
        ann = ModelAnnotation.query.filter_by(model_id=model_id).one()
        theta, phi, radius = ann.camera_orbit.split(" ")
        assert theta.endswith("rad") and phi.endswith("rad")
        assert radius.endswith("m") and abs(float(radius[:-1]) - 0.41755) < 1e-5
        assert all(part.endswith("m") for part in ann.camera_target.split(" "))
        assert ann.camera_fov.endswith("deg") and abs(float(ann.camera_fov[:-3]) - 18.769) < 1e-3
        assert len(ann.camera_orbit) <= 64 and len(ann.camera_target) <= 96 and len(ann.camera_fov) <= 16


def test_unparseable_camera_is_dropped_not_truncated(client, app):
    register(client)
    model_id = _model(app)
    assert _post(client, model_id, {"orbit": "nonsense", "target": "1 2", "fov": "wide"}).status_code == 201
    with app.app_context():
        ann = ModelAnnotation.query.filter_by(model_id=model_id).one()
        assert (ann.camera_orbit, ann.camera_target, ann.camera_fov) == (None, None, None)
