"""First-run journey fixes: private-project sharing, visibility switch,
graceful private pages, the shorter new-project form and upload feedback."""
import re
import uuid

from models import Model3D, Paper, QRLink, User, db
from tests.conftest import register, upload_file_bytes


def _project(app, visibility="private", email="user@example.com", with_model=True):
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        paper = Paper(title="Knee joint", slug=f"knee-{uuid.uuid4().hex[:6]}", user_id=user.id,
                      visibility=visibility, is_public=visibility == "public")
        if visibility == "unlisted":
            paper.share_token = uuid.uuid4().hex
        db.session.add(paper)
        db.session.flush()
        model_id = public_id = None
        if with_model:
            model = Model3D(id=str(uuid.uuid4()), paper_id=paper.id, user_id=user.id,
                            glb_path="converted/x/model.glb", processing_status="ready",
                            original_filename="knee_scan.stl", public_id=uuid.uuid4().hex[:16])
            db.session.add(model)
            model_id, public_id = model.id, model.public_id
        db.session.commit()
        return paper.slug, model_id, public_id


# --- Private projects -----------------------------------------------------------

def test_private_project_page_warns_and_hides_share_links(client, app):
    register(client)
    slug, _, _ = _project(app)
    html = client.get(f"/projects/{slug}").data.decode()
    assert "Only you can open this project." in html
    assert "Preview as visitor" not in html
    assert "/qr-image/paper/" not in html  # no printable project QR while private


def test_visitor_of_private_project_gets_explanation_not_404(client, app):
    register(client)
    slug, model_id, public_id = _project(app)
    client.post("/auth/logout")
    for path in (f"/view/{model_id}", f"/m/{public_id}", f"/p/{slug}"):
        resp = client.get(path)
        assert resp.status_code == 403, path
        body = resp.data.decode()
        assert "This project is private." in body
        assert "Knee joint" not in body and "knee_scan" not in body


def test_deleted_project_still_404s(client, app):
    register(client)
    slug, model_id, _ = _project(app)
    with app.app_context():
        Paper.query.filter_by(slug=slug).one().status = "deleted"
        db.session.commit()
    client.post("/auth/logout")
    assert client.get(f"/view/{model_id}").status_code == 404


def test_qr_print_pages_warn_when_project_is_private(client, app):
    register(client)
    slug, model_id, _ = _project(app)
    with app.app_context():
        paper_id = Paper.query.filter_by(slug=slug).one().id
    assert "This project is private." in client.get(f"/qr-print/{model_id}").data.decode()
    assert "This project is private." in client.get(f"/qr-print/paper/{paper_id}").data.decode()


# --- Visibility switch -------------------------------------------------------------

def test_owner_switches_visibility_from_project_page(client, app):
    register(client)
    slug, _, _ = _project(app)
    resp = client.post(f"/projects/{slug}/visibility", data={"visibility": "unlisted"})
    assert resp.status_code in (302, 303)
    with app.app_context():
        paper = Paper.query.filter_by(slug=slug).one()
        assert paper.visibility == "unlisted" and paper.share_token and not paper.is_public
    html = client.get(f"/projects/{slug}").data.decode()
    # One name for the state everywhere ("Review link"), and the preview goes
    # to the share URL that visitors can actually open.
    assert "Unlisted" not in html
    assert re.search(r"<span>Visibility</span>\s*<strong>Review link</strong>", html)
    assert f'/share/{paper.share_token}' in html
    client.post(f"/projects/{slug}/visibility", data={"visibility": "public"})
    with app.app_context():
        paper = Paper.query.filter_by(slug=slug).one()
        assert paper.visibility == "public" and paper.is_public


def test_visibility_switch_is_owner_only_and_validated(client, app):
    register(client)
    slug, _, _ = _project(app)
    assert client.post(f"/projects/{slug}/visibility", data={"visibility": "everyone"}).status_code in (302, 303)
    with app.app_context():
        assert Paper.query.filter_by(slug=slug).one().visibility == "private"
    client.post("/auth/logout")
    register(client, email="other@example.com")
    assert client.post(f"/projects/{slug}/visibility", data={"visibility": "public"}).status_code == 403


# --- Project page actions ---------------------------------------------------------

def test_project_without_models_offers_add_first_model(client, app):
    register(client)
    slug, _, _ = _project(app, with_model=False)
    html = client.get(f"/projects/{slug}").data.decode()
    assert html.count('href="#add-model"') >= 2  # share panel + empty state


def test_free_capacity_notice_does_not_send_users_to_an_administrator(client, app):
    register(client)
    slug, _, _ = _project(app)
    html = client.get(f"/projects/{slug}").data.decode()
    assert "Plan capacity reached" in html
    assert "ask an administrator" not in html
    assert "per topic" not in html


# --- New project form ---------------------------------------------------------------

def test_new_project_form_puts_model_before_optional_sections(client):
    register(client)
    html = client.get("/projects/new").data.decode()
    title_at = html.index('name="title"')
    model_at = html.index('name="model_file"')
    details_at = html.index("form-optional-section")
    assert title_at < model_at < details_at
    # Optional sections start collapsed for a new project.
    assert not re.search(r'class="form-optional-section"\s+open', html)
    assert '<label class="field-label" for="model_display_name">Model name</label>' in html


def test_edit_form_opens_optional_sections(client, app):
    register(client)
    slug, _, _ = _project(app)
    html = client.get(f"/projects/{slug}/edit").data.decode()
    assert len(re.findall(r'class="form-optional-section"\s+open', html)) == 2


def test_failed_first_upload_says_project_was_saved(client, app):
    register(client)
    resp = client.post(
        "/projects/new",
        data={
            "title": "Broken upload",
            "visibility": "private",
            "model_file": upload_file_bytes(b"not a glb", "broken.glb"),
            "source_unit": "embedded",
            "compliance_confirm": "yes",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].endswith("#add-model")
    page = client.get(resp.headers["Location"].split("#")[0]).data.decode()
    assert "Project saved, but the model could not be added" in page
    with app.app_context():
        assert Paper.query.filter_by(title="Broken upload").count() == 1
