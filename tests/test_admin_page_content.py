"""Admin Projects page (/admin/content) clean-up."""
from models import Paper, db
from tests.conftest import create_user, login


def _setup(client):
    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin User")
        admin.is_admin = True
        owner = create_user(email="owner@example.com", username="Owner")
        live = Paper(title="Live Project", slug="live-project", user_id=owner.id, visibility="unlisted", is_public=False)
        gone = Paper(title="Gone Project", slug="gone-project", user_id=owner.id, status="deleted", visibility="private", is_public=False)
        db.session.add_all([live, gone])
        db.session.commit()
        ids = live.id, gone.id
    login(client, email="admin@example.com")
    return ids


def test_visibility_change_on_deleted_project_is_truthful(client):
    _, gone_id = _setup(client)
    response = client.post(
        f"/admin/papers/{gone_id}/visibility",
        data={"visibility": "public", "status": "deleted"},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert "Project updated" not in html
    assert "stays private" in html
    with client.application.app_context():
        paper = db.session.get(Paper, gone_id)
        assert paper.status == "deleted"
        assert paper.visibility == "private"
        assert paper.deleted_at is None  # not re-stamped


def test_page_has_no_stats_block_status_column_or_deleted_selects(client):
    live_id, gone_id = _setup(client)
    html = client.get("/admin/content").get_data(as_text=True)
    assert "Project and model statistics" not in html
    assert "admin-sparkline" not in html
    assert "<th>Status</th>" not in html
    assert "2 projects" in html
    assert "admin-hide-sm" in html
    assert f"/admin/papers/{live_id}/visibility" in html
    assert f"/admin/papers/{gone_id}/visibility" not in html
    assert f"/admin/papers/{gone_id}/restore" in html
    assert "Review link" in html


def test_export_link_drops_page_param(client):
    _setup(client)
    html = client.get("/admin/content?paper_q=live&page=1").get_data(as_text=True)
    assert "export.csv?paper_q=live" in html
    assert "export.csv?paper_q=live&amp;page" not in html
