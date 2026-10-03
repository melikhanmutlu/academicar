"""Dashboard visibility chips, one name for projects, /projects/ URLs after
model actions, and a compact public title card."""
import re
import uuid

from models import Paper, User, db
from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes


def _paper(app, **fields):
    with app.app_context():
        user = User.query.filter_by(email="user@example.com").first()
        paper = Paper(title=fields.pop("title", "Row test"), slug=f"row-{uuid.uuid4().hex[:6]}",
                      user_id=user.id, **fields)
        db.session.add(paper)
        db.session.commit()
        return paper.slug


def test_dashboard_rows_show_visibility_instead_of_a_dash(client, app):
    register(client)
    _paper(app, title="Private row", visibility="private", is_public=False)
    _paper(app, title="Public row", visibility="public", is_public=True, institution="Cerrahpaşa")
    html = client.get("/dashboard").data.decode()
    assert re.search(r'publication-visibility is-private">Private<', html)
    assert re.search(r'publication-visibility is-public">Public<', html)
    assert "Cerrahpaşa" in html
    assert not re.search(r"<p>\s*-\s*</p>", html)


def test_edit_form_talks_about_projects(client, app):
    register(client)
    slug = _paper(app)
    html = client.get(f"/projects/{slug}/edit").data.decode()
    assert "Delete project" in html
    assert "Delete Publication" not in html and "this publication" not in html


def test_model_upload_returns_to_projects_url(client, app):
    register(client)
    slug = _paper(app)
    resp = client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": upload_file_bytes(valid_ascii_stl_bytes(), "part.stl"),
            "source_unit": "mm",
            "compliance_confirm": "yes",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code in (302, 303)
    assert f"/projects/{slug}" in resp.headers["Location"]


def test_public_page_without_details_has_no_empty_meta_block(client, app):
    register(client)
    slug = _paper(app, visibility="public", is_public=True)
    html = client.get(f"/p/{slug}").data.decode()
    assert "public-hero-grid" in html
    assert '<div class="paper-meta mt-5">' not in html
