"""Regression tests for the v6.4 fifth audit fixes.

Covers: admin status validation, abstract length limit, model upload
display_name/description truncation, password max length, make_slug guard.
"""
from models import Paper, User, db


def test_admin_paper_status_rejects_invalid(client):
    """Admin visibility update should reject arbitrary status values."""
    from tests.conftest import login, register

    register(client, email="admin-status@example.com")
    app = client.application
    with app.app_context():
        user = User.query.filter_by(email="admin-status@example.com").one()
        user.is_admin = True
        paper = Paper(title="Status Paper", slug="status-paper", user_id=user.id)
        db.session.add(paper)
        db.session.commit()
        paper_id = paper.id

    login(client, email="admin-status@example.com")
    resp = client.post(
        f"/admin/papers/{paper_id}/visibility",
        data={"is_public": "1", "status": "hacked"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.status != "hacked"


def test_validate_paper_form_rejects_long_abstract(client):
    """Abstract longer than 5000 chars should be rejected."""
    from app import validate_paper_form

    form = {
        "title": "Test Paper",
        "authors": "Author",
        "abstract": "X" * 5001,
        "field": "",
        "doi": "",
        "institution": "",
        "pmid": "",
        "year": "2024",
        "visibility": "public",
    }
    _, errors = validate_paper_form(form)
    assert any("Abstract" in e for e in errors)


def test_validate_paper_form_accepts_normal_abstract(client):
    """Abstract within 5000 chars should pass."""
    from app import validate_paper_form

    form = {
        "title": "Test Paper",
        "authors": "Author",
        "abstract": "Y" * 4999,
        "field": "",
        "doi": "",
        "institution": "",
        "pmid": "",
        "year": "2024",
        "visibility": "public",
    }
    _, errors = validate_paper_form(form)
    assert not any("Abstract" in e for e in errors)


def test_password_max_length_rejected(client):
    """Password longer than 1024 chars should be rejected."""
    from tests.conftest import login, register

    register(client, email="pwmax@example.com", password="OldPassword1")
    login(client, email="pwmax@example.com", password="OldPassword1")
    resp = client.post(
        "/account/password",
        data={
            "current_password": "OldPassword1",
            "new_password": "A" * 1025,
            "confirm_password": "A" * 1025,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"at most" in resp.data or b"1024" in resp.data
