"""/admin/access is QR management only: no viewer analytics, strict status POST."""
from datetime import UTC, datetime

from models import AuditLog, Model3D, Paper, QRLink, db
from tests.conftest import create_user, login


def _seed(client, qr_status="active"):
    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin User")
        admin.is_admin = True
        db.session.flush()
        paper = Paper(title="P", slug="p-access", user_id=admin.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="access-model", paper_id=paper.id, user_id=admin.id,
            glb_path="model.glb", processing_status="ready", file_size=1000,
        )
        db.session.add(model)
        db.session.flush()
        qr = QRLink(model_id=model.id, public_id="pubaccess1", status=qr_status,
                    last_resolved_at=datetime.now(UTC).replace(tzinfo=None))
        db.session.add(qr)
        db.session.add(AuditLog(event_type="public_model_viewed", resource_id=model.id))
        db.session.commit()
        return qr.id


def test_access_page_has_no_viewer_statistics(client):
    _seed(client)
    login(client, "admin@example.com")
    html = client.get("/admin/access").get_data(as_text=True)
    assert "Viewer opens" not in html
    assert "Most viewed models" not in html
    assert "viewer trend" not in html
    assert "Resolved, 30 days" in html
    assert "1 link" in html


def test_empty_or_missing_status_does_not_reenable_qr(client):
    qr_id = _seed(client, qr_status="disabled")
    login(client, "admin@example.com")
    for data in ({}, {"status": ""}, {"status": "  "}, {"status": "bogus"}):
        resp = client.post(f"/admin/qr-links/{qr_id}/status", data=data)
        assert resp.status_code == 302
        with client.application.app_context():
            assert db.session.get(QRLink, qr_id).status == "disabled"


def test_status_post_disables_and_skips_noop_audit(client):
    qr_id = _seed(client)
    login(client, "admin@example.com")
    client.post(f"/admin/qr-links/{qr_id}/status", data={"status": "disabled"})
    client.post(f"/admin/qr-links/{qr_id}/status", data={"status": "disabled"})
    with client.application.app_context():
        assert db.session.get(QRLink, qr_id).status == "disabled"
        assert AuditLog.query.filter_by(event_type="admin_qr_status_changed").count() == 1
