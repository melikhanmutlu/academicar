"""Author impact report: period switch, CSV, print layout, monthly email."""
import csv
import io
import uuid
from datetime import UTC, datetime

from models import AnalyticsEvent, AuditLog, Model3D, Paper, User, db
from tests.conftest import register


def _model_with_views(app, when, views=3, owner_email="user@example.com"):
    with app.app_context():
        owner = User.query.filter_by(email=owner_email).one()
        paper = Paper(title="Impact project", slug=f"imp-{uuid.uuid4().hex[:8]}", user_id=owner.id,
                      visibility="public", is_public=True)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(id=str(uuid.uuid4()), paper_id=paper.id, user_id=owner.id, display_name="=Femur",
                        glb_path="x.glb", license_type="academic", processing_status="ready")
        db.session.add(model)
        for i in range(views):
            db.session.add(AnalyticsEvent(event_name="model_viewed", owner_user_id=owner.id, model_id=model.id,
                                          visitor_hash=f"v{i}", device_type="mobile", occurred_at=when))
        db.session.add(AnalyticsEvent(event_name="qr_scanned", owner_user_id=owner.id, model_id=model.id, occurred_at=when))
        # Not a viewer event: must not show up in the audience breakdowns.
        db.session.add(AnalyticsEvent(event_name="model_uploaded", owner_user_id=owner.id, device_type="desktop", occurred_at=when))
        db.session.commit()
        return owner.id, model.id


def test_insights_periods_csv_and_print(client, app):
    register(client)
    _model_with_views(app, datetime.now(UTC).replace(tzinfo=None))
    html = client.get("/insights?days=90").get_data(as_text=True)
    assert 'aria-current="page" class="is-active">90 days' in html
    assert "Print / PDF" in html and "/insights/export.csv?days=90" in html
    assert ">desktop<" not in html  # uploads are not audience data

    response = client.get("/insights/export.csv?days=7")
    assert response.mimetype == "text/csv"
    rows = list(csv.DictReader(io.StringIO(response.get_data(as_text=True))))
    assert rows[0]["views"] == "3" and rows[0]["qr_scans"] == "1"
    assert rows[0]["model"] == "'=Femur"  # formula injection neutralised


def test_monthly_impact_email_once_per_month_with_unsubscribe(client, app, monkeypatch):
    from lifecycle import send_monthly_impact_reports

    register(client)
    sent = []
    monkeypatch.setattr("utils.email.send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    owner_id, _ = _model_with_views(app, datetime(2026, 9, 15, 12, 0))
    now = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    with app.app_context():
        assert send_monthly_impact_reports(now) == 1
        assert send_monthly_impact_reports(now) == 0  # once per month
        assert AuditLog.query.filter_by(event_type="impact_report_sent").count() == 1
    to, subject, body = sent[0]
    assert to == "user@example.com" and "September 2026" in subject
    assert "Views: 3" in body and "QR scans: 1" in body and "Most viewed: =Femur (3 views)" in body

    link = next(line for line in body.splitlines() if "/unsubscribe/" in line).split(": ", 1)[1]
    path = "/" + link.split("/", 3)[3]
    client.post("/auth/logout")
    assert b"Stop monthly emails" in client.get(path).data  # GET does not unsubscribe
    with app.app_context():
        assert db.session.get(User, owner_id).impact_report_opt_out is False
    client.post(path)
    with app.app_context():
        assert db.session.get(User, owner_id).impact_report_opt_out is True
        db.session.query(AuditLog).filter_by(event_type="impact_report_sent").delete()
        db.session.commit()
        assert send_monthly_impact_reports(now) == 0  # opted out
    assert client.get("/account/impact-report/unsubscribe/forged").status_code == 404


def test_profile_toggle_for_impact_emails(client, app):
    register(client)
    assert b"Monthly impact summary" in client.get("/profile").data
    client.post("/account/impact-report", data={})
    with app.app_context():
        assert User.query.one().impact_report_opt_out is True
    client.post("/account/impact-report", data={"impact_report": "on"})
    with app.app_context():
        assert User.query.one().impact_report_opt_out is False
