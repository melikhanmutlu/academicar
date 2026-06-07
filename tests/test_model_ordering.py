"""Regression: models inside a publication must always list in addition order
(by created_at), and must NOT jump position when one is updated (e.g. recolor).
On Postgres an UPDATE rewrites the row's physical location, so an unordered
relationship would push the edited model to the end — this pins the order."""
import uuid
from datetime import UTC, datetime, timedelta

from models import Model3D, Paper, User, db


def _paper_with_models(app, created_at_order):
    """Create a paper and add models whose ``created_at`` values are supplied in
    a deliberately scrambled insertion order. Returns (paper_id, {label: id})."""
    with app.app_context():
        user = User(email=f"order-{uuid.uuid4().hex[:6]}@example.com", username="O")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(title="P", slug=f"ord-{uuid.uuid4().hex[:6]}", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        base = datetime(2026, 1, 1, tzinfo=UTC)
        ids = {}
        for label, seconds in created_at_order:
            mid = str(uuid.uuid4())
            db.session.add(
                Model3D(
                    id=mid,
                    paper_id=paper.id,
                    user_id=user.id,
                    display_name=label,
                    glb_path=f"converted/{mid}/model.glb",
                    created_at=base + timedelta(seconds=seconds),
                )
            )
            ids[label] = mid
        db.session.commit()
        return paper.id, ids


def test_models_list_in_addition_order(app):
    # Insert C (newest), then A (oldest), then B (middle) — scrambled on purpose.
    paper_id, _ = _paper_with_models(app, [("C", 30), ("A", 10), ("B", 20)])
    with app.app_context():
        paper = db.session.get(Paper, paper_id)
        assert [m.display_name for m in paper.models] == ["A", "B", "C"]


def test_order_is_stable_after_update(app):
    paper_id, ids = _paper_with_models(app, [("A", 10), ("B", 20), ("C", 30)])
    with app.app_context():
        # Update the middle model the way a recolor does (in-place field change).
        middle = db.session.get(Model3D, ids["B"])
        middle.appearance_color = "#ff0000"
        middle.file_size = 12345
        db.session.commit()
    with app.app_context():
        paper = db.session.get(Paper, paper_id)
        assert [m.display_name for m in paper.models] == ["A", "B", "C"]


def test_first_model_is_oldest(app):
    # The template uses paper.models[0] as the representative model; it must be
    # the first one added, not whichever row was touched last.
    paper_id, ids = _paper_with_models(app, [("A", 10), ("B", 20)])
    with app.app_context():
        # Touch A after B so, without ordering, A could surface last.
        a = db.session.get(Model3D, ids["A"])
        a.appearance_color = "#00ff00"
        db.session.commit()
    with app.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.models[0].display_name == "A"
