import csv
import io
from datetime import datetime

import pytest

from app import mask_audit_details, mask_email, mask_ip
from models import AuditLog, db
from tests.conftest import create_user, login


@pytest.fixture
def admin_client(client):
    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin")
        admin.is_admin = True
        db.session.commit()
    login(client, email="admin@example.com")
    with client.application.app_context():
        AuditLog.query.delete()  # drop the login's own audit row
        db.session.commit()
    return client


def _add(**kw):
    ts = kw.pop("timestamp", None) or datetime(2026, 5, 10, 12, 0)
    db.session.add(AuditLog(timestamp=ts, **kw))


def _csv(client, qs=""):
    body = client.get("/admin/logs/export.csv" + qs).get_data(as_text=True)
    return list(csv.DictReader(io.StringIO(body))), body


def test_mask_helpers():
    assert mask_email("jane@example.com") == "j***@example.com"
    assert mask_ip("203.0.113.77") == "203.0.113.0/24"
    assert mask_ip("2001:db8:abcd:12::1") == "2001:db8:abcd::/48"
    assert mask_audit_details("contact_message_submitted", {"from_email": "a@b.org"}) == {"from_email": "a***@b.org"}
    assert mask_audit_details("institution_inquiry_submitted", {"message": "hello"}) == {"message": "[5 chars]"}
    assert mask_audit_details("user_login_failed", {"email": "Secret pass1"}) == {"email": "***"}
    assert mask_audit_details("user_login_failed", {"email": "x@corp.edu"}) == {"email": "***@corp.edu"}


def test_page_and_csv_mask_sensitive_data(admin_client):
    with admin_client.application.app_context():
        _add(event_type="institution_inquiry_submitted", ip_address="198.51.100.9",
             details={"email": "lead@uni.edu", "message": "please call me secretly"})
        _add(event_type="user_login_failed", details={"email": "hunter2pass"})
        db.session.commit()
    html = admin_client.get("/admin/logs").get_data(as_text=True)
    rows, body = _csv(admin_client)
    for text in (html, body):
        assert "lead@uni.edu" not in text
        assert "secretly" not in text
        assert "198.51.100.9" not in text
        assert "hunter2pass" not in text
    assert "l***@uni.edu" in html and "198.51.100.0/24" in html and "[23 chars]" in body
    with admin_client.application.app_context():
        assert AuditLog.query.filter_by(event_type="user_login_failed").one().details == {"email": "hunter2pass"}


def test_default_actions_view_hides_noise_and_all_shows_it(admin_client):
    with admin_client.application.app_context():
        for ev in ("public_model_viewed", "qr_resolved", "admin_user_detail_viewed", "admin_backup_downloaded", "user_registered"):
            _add(event_type=ev)
        db.session.commit()
    assert [r["event_type"] for r in _csv(admin_client)[0]] == ["user_registered"]
    assert len(_csv(admin_client, "?audit_event=all")[0]) >= 5
    assert [r["event_type"] for r in _csv(admin_client, "?audit_event=qr_resolved")[0]] == ["qr_resolved"]
    html = admin_client.get("/admin/logs").get_data(as_text=True)
    assert "User registered" in html and "Public model viewed" not in html.split("<tbody>")[1]


def test_date_range_inclusive_on_page_and_csv(admin_client):
    with admin_client.application.app_context():
        _add(event_type="e_one", timestamp=datetime(2026, 5, 1, 0, 0))
        _add(event_type="e_two", timestamp=datetime(2026, 5, 2, 23, 59))
        _add(event_type="e_three", timestamp=datetime(2026, 5, 3, 0, 0))
        db.session.commit()
    rows, _ = _csv(admin_client, "?audit_from=2026-05-01&audit_to=2026-05-02")
    assert sorted(r["event_type"] for r in rows) == ["e_one", "e_two"]
    body = admin_client.get("/admin/logs?audit_from=2026-05-02&audit_to=2026-05-02").get_data(as_text=True).split("<tbody>")[1]
    assert "E two" in body and "E one" not in body and "E three" not in body
    assert len(_csv(admin_client, "?audit_from=garbage")[0]) == 3


def test_user_column_masked_email_and_deleted_vs_visitor(admin_client):
    with admin_client.application.app_context():
        u = create_user(email="jane@example.com", username="Jane")
        _add(event_type="paper_created", user_id=u.id)
        _add(event_type="account_deleted")
        _add(event_type="contact_form")
        db.session.commit()
        uid = u.id
    html = admin_client.get("/admin/logs").get_data(as_text=True)
    assert f"/admin/users/{uid}" in html and "j***@example.com" in html and "jane@example.com" not in html
    assert "Deleted user" in html and "Visitor / system" in html
    assert "<h2>Audit log</h2>" not in html


def test_csv_keeps_formula_guard_and_row_limit(admin_client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_CSV_EXPORT_ROW_LIMIT", 2)
    with admin_client.application.app_context():
        _add(event_type="=cmd", resource_id="1", timestamp=datetime(2026, 5, 12))
        _add(event_type="b_event")
        _add(event_type="c_event", timestamp=datetime(2026, 5, 9))
        db.session.commit()
    rows, body = _csv(admin_client)
    assert len(rows) == 2
    assert rows[0]["event_type"] == "'=cmd"
