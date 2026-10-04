"""Regressions from the logged-in app audit (v37)."""
import os
import uuid

from models import Model3D, Paper, User, db
from tests.conftest import register


def _ready_model(app, **fields):
    with app.app_context():
        owner = User.query.filter_by(email="user@example.com").first()
        paper = Paper(title="Audit project", slug=f"audit-{uuid.uuid4().hex[:8]}", user_id=owner.id,
                      is_public=True, visibility="public")
        db.session.add(paper)
        db.session.flush()
        model = Model3D(id=str(uuid.uuid4()), paper_id=paper.id, user_id=owner.id,
                        glb_path="converted/x/model.glb", license_type="academic",
                        processing_status="ready", **fields)
        db.session.add(model)
        db.session.commit()
        folder = os.path.join(app.config["CONVERTED_FOLDER"], model.id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "model.glb"), "wb") as fh:
            fh.write(b"glTF")
        return model.id, paper.slug


def test_account_delete_confirm_is_not_shadowed_by_the_confirm_input(client):
    """Inside an inline handler `confirm` resolves to <input name="confirm">,
    so the dialog threw and the form submitted without asking."""
    register(client)
    html = client.get("/profile").get_data(as_text=True)
    assert 'name="confirm"' in html
    assert 'onsubmit="return window.confirm(' in html


def test_failed_replacement_keeps_model_card_usable(client, app):
    register(client)
    model_id, slug = _ready_model(app)
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        model.processing_status = "replacement_failed"
        model.replacement_status = "replacement_failed"
        model.replacement_error = "Mesh has no faces."
        db.session.commit()
    html = client.get(f"/projects/{slug}").get_data(as_text=True)
    assert "previous version is still live" in html
    assert "Mesh has no faces." in html
    assert "Processing model..." not in html
    assert f'href="/view/{model_id}"' in html
    assert "Replacement_Failed" not in html
    assert client.get(f"/models/{model_id}/edit").status_code == 200


def test_model_edit_page_sends_appearance_change_flags(client, app):
    register(client)
    model_id, _ = _ready_model(app)
    html = client.get(f"/models/{model_id}/edit").get_data(as_text=True)
    assert 'name="color_changed" value="0"' in html
    assert 'name="finish_changed" value="0"' in html


def test_public_project_with_only_expired_models_says_so(client, app):
    from datetime import UTC, datetime, timedelta
    register(client)
    _model_id, slug = _ready_model(app, access_expires_at=datetime.now(UTC) - timedelta(days=1))
    client.post("/auth/logout")
    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert "Models temporarily unavailable." in html
    assert "No models available yet." not in html


def test_anonymous_header_links_product_pages(client):
    html = client.get("/about").get_data(as_text=True)
    header = html.split('<div class="site-nav-actions">', 1)[1].split("</div>", 1)[0]
    for label in ("Use cases", "Pricing", "Institutional", "Blog", "Log in", "Sign up"):
        assert label in header


def test_viewer_uses_only_generated_tailwind_opacity_steps(client):
    """Tailwind 3 only generates /N opacity modifiers on its scale (multiples
    of 5); /94 or /12 silently produced no CSS and left the viewer's info
    drawer and modals transparent."""
    import re
    from pathlib import Path

    for path in Path("templates").rglob("*.html"):
        for match in re.finditer(r"\b(?:bg|border|text|ring)-(?:\[[^\]\s]+\]|black|white|[a-z]+-\d{2,3})/(\d+)\b", path.read_text()):
            assert int(match.group(1)) % 5 == 0, f"{path}: {match.group(0)}"
