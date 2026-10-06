from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from models import AuditLog, db
from tests.test_admin_panel import _make_admin


def _log(event_type, days_ago=0):
    db.session.add(
        AuditLog(event_type=event_type, timestamp=datetime.now(UTC) - timedelta(days=days_ago))
    )


def test_security_counts_cover_30_days_and_link_to_the_log(client):
    _make_admin(client)
    with client.application.app_context():
        _log("user_login_failed")
        _log("user_login_failed", days_ago=40)
        _log("password_changed")
        _log("admin_user_role_changed")
        _log("admin_page_viewed")
        _log("admin_backup_downloaded")
        db.session.commit()
        queries = []

        def count(conn, cursor, statement, *a):
            if "audit_logs" in statement and "count(" in statement.lower():
                queries.append(statement)

        event.listen(db.engine, "before_cursor_execute", count)
        try:
            body = client.get("/admin/security").get_data(as_text=True)
        finally:
            event.remove(db.engine, "before_cursor_execute", count)
    assert "Critical system alerts" not in body
    assert "Risk monitor" not in body
    assert "audit_event=user_login_failed" in body
    assert "audit_event=password_changed" in body
    assert "<strong>1</strong>" in body  # 30-day window excludes the old row
    # one grouped query + one admin_* LIKE count (+ shared sidebar/stat counts)
    assert len([q for q in queries if "GROUP BY" in q]) == 1
