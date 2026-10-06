"""Admin panel round-3 UI polish: shared muted rule, security link target,
sidebar footer, storage copy, and the "Project deleted" chip."""
import re
from pathlib import Path

import pytest

from app import create_app, limiter
from models import Model3D, Paper, User, db

ROOT = Path(__file__).resolve().parent.parent
ADMIN_CSS = (ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'r3.db'}",
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
        owner = User(email="owner@example.com", username="Owner")
        owner.set_password("password123")
        db.session.add_all([admin, owner])
        db.session.flush()
        paper = Paper(title="Gone", slug="gone", user_id=owner.id, status="deleted")
        db.session.add(paper)
        db.session.flush()
        db.session.add(
            Model3D(
                id="model-gone",
                paper_id=paper.id,
                user_id=owner.id,
                glb_path="converted/model-gone/model.glb",
                original_filename="gone.stl",
                processing_status="ready",
                license_type="free",
            )
        )
        db.session.commit()
    with app.test_client() as c:
        c.post("/auth/login", data={"email": "admin@example.com", "password": "password123"})
        yield c


def _page_blocks_start() -> int:
    return ADMIN_CSS.index("/* == page:")


def test_admin_muted_is_one_shared_rule():
    rules = [m.start() for m in re.finditer(r"^\.admin-muted\s*\{", ADMIN_CSS, re.M)]
    assert len(rules) == 1, "`.admin-muted` must be defined exactly once"
    assert rules[0] < _page_blocks_start(), "`.admin-muted` belongs above the per-page blocks"


def test_security_admin_actions_card_links_to_actions_view(client):
    html = client.get("/admin/security").get_data(as_text=True)
    assert "audit_q=admin_" not in html
    card = re.search(r'<a class="admin-insight-card"\s+href="([^"]+)"\s+title="([^"]+)">\s*<span>Admin actions</span>', html)
    assert card, "Admin actions card missing"
    href, title = card.group(1), card.group(2)
    assert href.endswith("/admin/logs?audit_event=actions")
    assert "all time" in title
    # The other cards still deep-link to their single event type.
    assert "/admin/logs?audit_event=user_login_failed" in html


def test_models_list_marks_deleted_project_with_chip(client):
    html = client.get("/admin/models").get_data(as_text=True)
    assert "Project deleted" in html
    assert 'class="status-chip models-offline"' in html
    assert "/view/model-gone" not in html


def test_sidebar_footer_groups_profile_and_logout(client):
    html = client.get("/admin").get_data(as_text=True)
    assert "Back to site dashboard" in html
    foot = html.split('class="admin-sidebar-foot-links"', 1)[1].split("</div>", 1)[0]
    assert "Profile" in foot and "Log out" in foot


def test_storage_page_does_not_promise_largest_models(client):
    html = client.get("/admin/storage").get_data(as_text=True)
    assert "largest models" not in html.lower()


def test_security_grid_has_fixed_columns():
    block = ADMIN_CSS.split("/* == page:security == */", 1)[1].split("/* == end page:security == */", 1)[0]
    assert "repeat(4, minmax(0, 1fr))" in block
    assert "auto-fill" not in block


def test_analytics_metrics_scoped_rule(client):
    html = client.get("/admin/analytics").get_data(as_text=True)
    assert 'class="admin-metrics analytics-metrics"' in html
    block = ADMIN_CSS.split("/* == page:analytics == */", 1)[1]
    assert ".analytics-metrics .admin-metric" in block
