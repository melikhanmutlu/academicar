"""Tests for the model registry filter + sort toolbar on the publication detail
page. The controls are client-side, so these assert the server renders the
toolbar, per-row sort/filter data attributes, and conditional status options."""
import uuid
from datetime import UTC, datetime, timedelta

from models import Model3D, Paper, User, db
from tests.conftest import login


def _owned_paper_with_models(app, specs, email="registry@example.com"):
    """Create an owned paper with one model per spec dict
    ({name, size, status}); models are added in list order (created_at order)."""
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(email=email, username="R")
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
        paper = Paper(title="P", slug=f"reg-{uuid.uuid4().hex[:6]}", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i, spec in enumerate(specs):
            mid = str(uuid.uuid4())
            db.session.add(
                Model3D(
                    id=mid,
                    paper_id=paper.id,
                    user_id=user.id,
                    display_name=spec["name"],
                    glb_path=f"converted/{mid}/model.glb",
                    processing_status=spec.get("status", "ready"),
                    license_type="academic",
                    source_format="stl",
                    file_size=spec.get("size", 0),
                    appearance_color="#cccccc",
                    created_at=base + timedelta(seconds=i),
                    access_expires_at=base + timedelta(days=365),
                )
            )
        db.session.commit()
        return paper.slug


def test_toolbar_renders_with_multiple_models(client, app):
    slug = _owned_paper_with_models(
        app,
        [
            {"name": "Alpha", "size": 300, "status": "ready"},
            {"name": "Beta", "size": 100, "status": "ready"},
        ],
    )
    login(client, email="registry@example.com", password="password123")
    body = client.get(f"/papers/{slug}").get_data(as_text=True)
    # Match the rendered markup, not the selector strings inside the inline JS.
    assert 'class="registry-toolbar"' in body
    assert 'class="registry-search"' in body
    assert 'aria-label="Sort models"' in body
    # Sort options are present.
    assert "Date added" in body and "Name A" in body and "Largest first" in body


def test_rows_have_filter_sort_data_attributes(client, app):
    slug = _owned_paper_with_models(
        app,
        [
            {"name": "Alpha", "size": 300, "status": "ready"},
            {"name": "Beta", "size": 100, "status": "ready"},
        ],
        email="registry2@example.com",
    )
    login(client, email="registry2@example.com", password="password123")
    body = client.get(f"/papers/{slug}").get_data(as_text=True)
    # Name is lowercased for case-insensitive search; size + order drive sorting.
    assert 'data-name="alpha"' in body
    assert 'data-size="300"' in body
    assert 'data-order="0"' in body
    assert 'data-order="1"' in body


def test_status_filter_only_when_non_ready_present(client, app):
    # All-ready paper: no status dropdown (it would be noise).
    ready_slug = _owned_paper_with_models(
        app,
        [{"name": "A", "status": "ready"}, {"name": "B", "status": "ready"}],
        email="registry3@example.com",
    )
    login(client, email="registry3@example.com", password="password123")
    ready_body = client.get(f"/papers/{ready_slug}").get_data(as_text=True)
    assert 'aria-label="Filter models by status"' not in ready_body

    # Paper with a failed model: status dropdown appears with a Failed option.
    mixed_slug = _owned_paper_with_models(
        app,
        [{"name": "A", "status": "ready"}, {"name": "B", "status": "failed"}],
        email="registry3@example.com",
    )
    mixed_body = client.get(f"/papers/{mixed_slug}").get_data(as_text=True)
    assert 'aria-label="Filter models by status"' in mixed_body
    assert ">Failed<" in mixed_body


def test_no_toolbar_for_single_model(client, app):
    slug = _owned_paper_with_models(
        app,
        [{"name": "Solo", "status": "ready"}],
        email="registry4@example.com",
    )
    login(client, email="registry4@example.com", password="password123")
    body = client.get(f"/papers/{slug}").get_data(as_text=True)
    assert 'class="registry-toolbar"' not in body
