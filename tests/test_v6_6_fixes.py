"""Regression tests for the v6.6 seventh audit fixes.

Covers: admin mutation routes wrap db.session.commit() in try/except + rollback
so a commit failure produces a graceful danger flash + redirect instead of an
unhandled 500 with a broken session.
"""
from sqlalchemy.exc import SQLAlchemyError

from models import Paper, User, db


def _make_admin(client, email="admin6@example.com"):
    from tests.conftest import login, register

    register(client, email=email)
    app = client.application
    with app.app_context():
        user = User.query.filter_by(email=email).one()
        user.is_admin = True
        db.session.commit()
        user_id = user.id
    login(client, email=email)
    return app, user_id


def test_admin_role_update_commit_failure_is_graceful(client, monkeypatch):
    """If commit fails, the admin route should flash danger + redirect (no 500)."""
    app, admin_id = _make_admin(client)

    # Create a second user to change the role of.
    with app.app_context():
        target = User(email="target6@example.com", username="Target")
        target.set_password("password123")
        db.session.add(target)
        db.session.commit()
        target_id = target.id

    # Force the next commit to fail.
    original_commit = db.session.commit

    def boom():
        raise SQLAlchemyError("simulated commit failure")

    monkeypatch.setattr(db.session, "commit", boom)

    resp = client.post(
        f"/admin/users/{target_id}/role",
        data={"is_admin": "1"},
        follow_redirects=True,
    )

    # Should NOT be a 500 — the route catches the error and redirects.
    assert resp.status_code == 200
    assert b"Could not update" in resp.data

    # Restore commit and confirm the role change did NOT persist.
    monkeypatch.setattr(db.session, "commit", original_commit)
    with app.app_context():
        target = db.session.get(User, target_id)
        assert target.is_admin is False


def test_admin_paper_visibility_commit_failure_is_graceful(client, monkeypatch):
    """Visibility update should also degrade gracefully on commit failure."""
    app, admin_id = _make_admin(client, email="admin6b@example.com")

    with app.app_context():
        owner = User.query.filter_by(email="admin6b@example.com").one()
        paper = Paper(title="Vis Paper", slug="vis-paper-6", user_id=owner.id)
        db.session.add(paper)
        db.session.commit()
        paper_id = paper.id

    def boom():
        raise SQLAlchemyError("simulated commit failure")

    monkeypatch.setattr(db.session, "commit", boom)

    resp = client.post(
        f"/admin/papers/{paper_id}/visibility",
        data={"is_public": "1", "status": "active"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Could not update" in resp.data
