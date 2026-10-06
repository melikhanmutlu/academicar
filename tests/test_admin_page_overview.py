"""Admin overview (/admin): live-project scope for the watch lists, slimmed layout."""
from datetime import UTC, datetime, timedelta

from models import Model3D, Paper, db
from tests.conftest import create_user, login


def _seed(client):
    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin User")
        admin.is_admin = True
        member = create_user(email="member@example.com", username="Member")
        db.session.flush()
        live = Paper(title="Live", slug="live", user_id=member.id)
        gone = Paper(title="Gone", slug="gone", user_id=member.id, status="deleted")
        db.session.add_all([live, gone])
        db.session.flush()
        soon = datetime.now(UTC) + timedelta(days=5)
        for mid, paper, size in (("m-live", live, 500), ("m-gone", gone, 900)):
            db.session.add(Model3D(
                id=mid, paper_id=paper.id, user_id=member.id, glb_path="m.glb",
                display_name=f"name-{mid}", processing_status="ready", file_size=size,
                storage_limit_bytes=size, access_expires_at=soon,
            ))
        db.session.commit()


def _overview(client):
    _seed(client)
    login(client, email="admin@example.com")
    response = client.get("/admin")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_watch_lists_exclude_models_of_deleted_projects(client):
    html = _overview(client)
    assert "name-m-live" in html
    assert "name-m-gone" not in html


def test_near_limit_alert_counts_only_live_models(client):
    html = _overview(client)
    assert "1 model near storage limit" in html


def test_overview_is_slimmed_and_labels_are_clear(client):
    html = _overview(client)
    assert "Operations overview" in html
    for gone in ("Platform signals", "Live snapshot", "Source formats", "Job queue", "PDF coverage"):
        assert gone not in html
    assert "ready and in date" in html
    assert "Jobs waiting" in html
    assert "Queued jobs" not in html
