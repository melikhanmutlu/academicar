"""Admin layout: row editors sit in the action bar, phones get stacked table
rows with header labels, and the backup button lives in the section head."""
import re
from pathlib import Path

import pytest

from app import create_app, limiter
from models import Paper, User, db

ROOT = Path(__file__).resolve().parent.parent
ADMIN_CSS = (ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'layout.db'}",
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "CONVERTED_FOLDER": str(tmp_path / "converted"),
            "QR_FOLDER": str(tmp_path / "qr"),
            "PDF_FOLDER": str(tmp_path / "pdfs"),
            "SECRET_KEY": "test",
            "STORAGE_MIN_FREE_BYTES": 0,
        }
    )
    limiter.reset()
    with app.app_context():
        db.create_all()
        admin = User(email="admin@example.com", username="Admin", is_admin=True)
        admin.set_password("password123")
        db.session.add(admin)
        db.session.flush()
        db.session.add(Paper(title="Knee", slug="knee", user_id=admin.id))
        db.session.commit()
    with app.test_client() as c:
        c.post("/auth/login", data={"email": "admin@example.com", "password": "password123"})
        yield c


def test_project_metadata_editor_is_part_of_the_action_bar(client):
    html = client.get("/admin/content").get_data(as_text=True)
    actions = html.split('<div class="admin-actions">', 1)[1].split("</td>", 1)[0]
    assert '<details class="admin-editor">' in actions
    assert "<summary>Edit</summary>" in actions
    assert ".admin-actions > .admin-editor > summary" in ADMIN_CSS


def test_tables_stack_on_phones_with_header_labels(client):
    html = client.get("/admin/content").get_data(as_text=True)
    assert "setAttribute('data-label'" in html
    assert re.search(r"@media \(max-width: 760px\) \{\s*\.admin-table,", ADMIN_CSS)
    assert ".admin-table td[data-label]::before" in ADMIN_CSS


def test_backup_button_sits_in_the_section_head(client):
    html = client.get("/admin/backups").get_data(as_text=True)
    head = html.split('<div class="admin-section-head">', 1)[1].split('<div class="table-wrap">', 1)[0]
    assert "Create backup now" in head
    assert "admin-filter-bar" not in html.split('id="backups"', 1)[1]


def test_breakdown_rows_do_not_style_nested_blocks():
    style = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert ".admin-breakdown > div:not([class])" in style
    assert "\n.admin-breakdown div,\n" not in style
