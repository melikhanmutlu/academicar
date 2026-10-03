"""Regression tests for the second-round UX audit (2026-10-02, round 2)."""
import io
import os
import re
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

import app as app_module
from models import (
    AuditLog,
    InstitutionMember,
    Model3D,
    Paper,
    ProjectArticle,
    ProjectAttachment,
    QRLink,
    User,
    db,
)
from tests.conftest import login, register


def _user_id(app, email="user@example.com"):
    with app.app_context():
        return User.query.filter_by(email=email).first().id


def _make_paper(app, visibility="public", title="R2 Paper", email="user@example.com"):
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        paper = Paper(
            title=title,
            slug=f"r2-{uuid.uuid4().hex[:8]}",
            user_id=user.id,
            is_public=(visibility == "public"),
            visibility=visibility,
        )
        if visibility == "unlisted":
            paper.share_token = uuid.uuid4().hex
        db.session.add(paper)
        db.session.commit()
        return paper.id, paper.slug


def _make_model(app, paper_id, license_type="free", status="ready", expires=None):
    with app.app_context():
        paper = db.session.get(Paper, paper_id)
        model = Model3D(
            id=uuid.uuid4().hex,
            paper_id=paper.id,
            user_id=paper.user_id,
            glb_path="converted/test/model.glb",
            license_type=license_type,
            processing_status=status,
            access_expires_at=expires,
        )
        db.session.add(model)
        db.session.commit()
        return model.id


def _edit_payload(paper, **extra):
    data = {
        "project_type": paper.project_type or "research_project",
        "workflow_stage": paper.workflow_stage or "in_progress",
        "title": paper.title,
        "visibility": paper.visibility or "public",
    }
    data.update(extra)
    return data


# --- Project edit form ---------------------------------------------------------------

def test_edit_form_never_renders_none_for_empty_article_fields(client, app):
    register(client)
    paper_id, slug = _make_paper(app)
    with app.app_context():
        db.session.add(ProjectArticle(project_id=paper_id, title="Only a title", order_index=0))
        db.session.commit()
    html = client.get(f"/projects/{slug}/edit").data.decode()
    assert 'value="None"' not in html
    assert ">None</textarea>" not in html


def test_saving_project_keeps_article_that_only_has_a_pdf(client, app):
    register(client)
    paper_id, slug = _make_paper(app)
    with app.app_context():
        article = ProjectArticle(project_id=paper_id, pdf_path="article_x.pdf", order_index=0)
        db.session.add(article)
        db.session.commit()
        article_id = article.id
        paper = db.session.get(Paper, paper_id)
        payload = _edit_payload(paper, title="Renamed")
    payload.update({
        "article_id[]": str(article_id), "article_title[]": "", "article_authors[]": "",
        "article_year[]": "", "article_doi[]": "", "article_pmid[]": "", "article_abstract[]": "",
    })
    resp = client.post(f"/projects/{slug}/edit", data=payload)
    assert resp.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(ProjectArticle, article_id) is not None


def test_material_remove_does_not_submit_the_edit_form(client, app):
    register(client)
    paper_id, slug = _make_paper(app)
    with app.app_context():
        db.session.add(ProjectAttachment(project_id=paper_id, original_filename="deck.pdf", file_type="pdf", source_path="material_x.pdf"))
        db.session.commit()
    html = client.get(f"/projects/{slug}/edit").data.decode()
    assert "formaction=" not in html
    delete_form = re.search(r'<form[^>]*action="[^"]*/attachments/\d+/delete"[^>]*>', html)
    assert delete_form and "confirm(" in delete_form.group(0)


def test_visibility_change_regenerates_project_qr(client, app):
    register(client)
    paper_id, slug = _make_paper(app, visibility="public")
    assert client.get(f"/qr-image/paper/{paper_id}").status_code == 200
    qr_path = os.path.join(app.config["QR_FOLDER"], app_module.paper_qr_filename(paper_id))
    assert os.path.exists(qr_path)
    with app.app_context():
        paper = db.session.get(Paper, paper_id)
        payload = _edit_payload(paper, visibility="unlisted")
    assert client.post(f"/projects/{slug}/edit", data=payload).status_code in (302, 303)
    assert not os.path.exists(qr_path), "stale QR (pointing at the old URL) must be dropped"


# --- Share / review-link flow -----------------------------------------------------

def test_unlisted_reader_back_link_uses_share_url(client, app):
    register(client)
    paper_id, slug = _make_paper(app, visibility="unlisted")
    with app.app_context():
        attachment = ProjectAttachment(project_id=paper_id, original_filename="deck.pdf", file_type="pdf", source_path="material_x.pdf", preview_pdf_path="material_x.pdf")
        db.session.add(attachment)
        db.session.commit()
        attachment_id = attachment.id
        token = db.session.get(Paper, paper_id).share_token
    html = client.get(f"/p/{slug}/materials/{attachment_id}").data.decode()
    assert f"/share/{token}" in html
    assert f'href="/p/{slug}"' not in html


def test_unavailable_page_links_to_share_url_for_unlisted_project(client, app):
    register(client)
    paper_id, _slug = _make_paper(app, visibility="unlisted")
    model_id = _make_model(app, paper_id, expires=datetime.now(UTC) - timedelta(days=1))
    with app.app_context():
        token = db.session.get(Paper, paper_id).share_token
    client.post("/auth/logout")
    client.get(f"/share/{token}")
    resp = client.get(f"/view/{model_id}")
    assert resp.status_code == 410
    assert f"/share/{token}" in resp.data.decode()


# --- Unavailable page copy -------------------------------------------------------------

def test_expired_page_copy_is_plan_neutral_and_visitor_is_not_sent_to_pricing(client, app):
    register(client)
    paper_id, _slug = _make_paper(app)
    model_id = _make_model(app, paper_id, license_type="academic", expires=datetime.now(UTC) - timedelta(days=1))
    client.post("/auth/logout")
    html = client.get(f"/view/{model_id}").data.decode()
    assert "3-day" not in html
    assert 'href="/pricing" class="btn-primary"' not in html


def test_processing_page_refreshes_itself(client, app):
    register(client)
    paper_id, _slug = _make_paper(app)
    model_id = _make_model(app, paper_id, status="processing")
    client.post("/auth/logout")
    html = client.get(f"/view/{model_id}").data.decode()
    assert 'http-equiv="refresh"' in html


def test_disabled_qr_link_shows_explanation_not_generic_404(client, app):
    register(client)
    paper_id, _slug = _make_paper(app)
    model_id = _make_model(app, paper_id)
    with app.app_context():
        link = QRLink(model_id=model_id, public_id=uuid.uuid4().hex[:22], status="disabled", target_type="model")
        db.session.add(link)
        db.session.commit()
        public_id = link.public_id
    resp = client.get(f"/m/{public_id}")
    assert resp.status_code == 410
    assert b"disabled" in resp.data.lower()


# --- Public counts ----------------------------------------------------------------------

def test_public_page_counts_only_viewable_models(client, app):
    register(client)
    paper_id, slug = _make_paper(app)
    _make_model(app, paper_id)
    _make_model(app, paper_id, status="failed")
    client.post("/auth/logout")
    html = client.get(f"/p/{slug}").data.decode()
    assert "1 model available" in html


# --- Accounts -------------------------------------------------------------------------------

def test_institution_member_can_delete_own_account(client, app):
    from tests.test_institutions import create_institution

    register(client)
    with app.app_context():
        institution = create_institution()
        user = User.query.filter_by(email="user@example.com").first()
        db.session.add(InstitutionMember(institution_id=institution.id, user_id=user.id, role="member"))
        db.session.commit()
    resp = client.post("/account/delete", data={"confirm": "DELETE", "current_password": "password123"}, follow_redirects=True)
    assert b"permanently deleted" in resp.data
    with app.app_context():
        assert User.query.filter_by(email="user@example.com").first() is None
        assert InstitutionMember.query.count() == 0


def test_profile_stats_ignore_deleted_projects(client, app):
    register(client)
    _make_paper(app, title="Kept")
    paper_id, _ = _make_paper(app, title="Gone")
    with app.app_context():
        paper = db.session.get(Paper, paper_id)
        paper.status = "deleted"
        db.session.commit()
    html = client.get("/profile").data.decode()
    stats = re.search(r'data-stat="projects"[^>]*>\s*(\d+)', html)
    assert stats and stats.group(1) == "1"


# --- Institutional inquiry ------------------------------------------------------------------

def test_institutional_inquiry_reports_mail_failure_and_keeps_message(client, app, monkeypatch):
    import utils.email

    monkeypatch.setattr(utils.email, "send_email", lambda *a, **k: False)
    resp = client.post(
        "/institutional",
        data={"institution_name": "Uni", "contact_name": "Ada", "email": "ada@uni.edu", "message": "We need 40 seats."},
        follow_redirects=True,
    )
    assert b"received your inquiry" not in resp.data
    assert app.config.get("CONTACT_EMAIL", "info@academicar.com").encode() in resp.data or b"@" in resp.data
    with app.app_context():
        entry = AuditLog.query.filter_by(event_type="institution_inquiry_submitted").first()
        assert entry is not None and entry.details.get("message") == "We need 40 seats."


def test_institutional_inquiry_validation_keeps_typed_values(client):
    resp = client.post("/institutional", data={"institution_name": "Uni", "contact_name": "Ada", "email": "not-an-email", "message": "Hello there"})
    assert resp.status_code == 200
    html = resp.data.decode()
    assert 'value="Uni"' in html and "Hello there" in html


# --- Institution join page ------------------------------------------------------------------

def test_join_page_warns_about_domain_before_submit(client, app):
    from tests.test_institutions import create_institution, make_invite

    register(client, email="outsider@gmail.com")
    with app.app_context():
        token = make_invite(create_institution(email_domains="boun.edu.tr")).token
    html = client.get(f"/institution/join/{token}").data.decode()
    assert "boun.edu.tr" in html
    assert not re.search(r'<button[^>]*type="submit"[^>]*>\s*Join', html)


# --- Insights -------------------------------------------------------------------------------

def test_insights_does_not_point_users_to_admin(client, app):
    register(client)
    paper_id, _ = _make_paper(app)
    _make_model(app, paper_id)
    with app.app_context():
        from licensing import get_license_plan
        from models import LicensePlanConfig
        plan = get_license_plan("free")
        db.session.add(LicensePlanConfig(key="free", label=plan.label, price_usd_cents=0, duration_days=plan.duration_days,
                                         storage_limit_bytes=plan.storage_limit_bytes, max_models_per_project=1,
                                         is_purchasable=True, features=[]))
        db.session.commit()
        from licensing import refresh_license_plan_cache
        refresh_license_plan_cache()
    html = client.get("/insights").data.decode()
    assert "Not in this plan" in html
    assert "Admin → Plans" not in html
    assert "/upgrade" in html


# --- Presentation previews run in the worker ----------------------------------------------

def _pptx_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/presentation.xml", "<p/>")
    return buf.getvalue()


def test_presentation_preview_is_queued_not_converted_in_request(client, app, monkeypatch):
    register(client)
    paper_id, slug = _make_paper(app)
    calls = []
    monkeypatch.setattr(app_module, "convert_presentation_to_pdf", lambda *a, **k: calls.append(a) or None)
    app.config["TESTING"] = False
    app.config["DEV_INLINE_JOBS"] = False
    try:
        with app.app_context():
            paper = db.session.get(Paper, paper_id)
            payload = _edit_payload(paper)
        payload["materials[]"] = (io.BytesIO(_pptx_bytes()), "deck.pptx")
        resp = client.post(f"/projects/{slug}/edit", data=payload, content_type="multipart/form-data")
    finally:
        app.config["TESTING"] = True
    assert resp.status_code in (302, 303)
    assert calls == [], "LibreOffice must not run inside the web request"
    with app.app_context():
        attachment = ProjectAttachment.query.filter_by(project_id=paper_id).first()
        assert attachment.preview_status == "pending"

    converted = []
    monkeypatch.setattr(app_module, "convert_presentation_to_pdf", lambda src, name: converted.append(name) or name)
    assert app_module.process_next_attachment_preview(app) is True
    with app.app_context():
        attachment = ProjectAttachment.query.filter_by(project_id=paper_id).first()
        assert attachment.preview_status == "ready"
        assert attachment.preview_pdf_path == converted[0]


def test_reader_does_not_show_developer_wording(client, app):
    register(client)
    paper_id, slug = _make_paper(app)
    with app.app_context():
        attachment = ProjectAttachment(project_id=paper_id, original_filename="deck.pptx", file_type="pptx", source_path="material_x.pptx", preview_status="failed")
        db.session.add(attachment)
        db.session.commit()
        attachment_id = attachment.id
    html = client.get(f"/p/{slug}/materials/{attachment_id}").data.decode()
    assert "LibreOffice" not in html
    assert "worker" not in html.lower()


# --- CSRF handler follow-ups ---------------------------------------------------------------

def test_csrf_failure_on_public_form_returns_to_form_not_login(client, app):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post("/institutional", data={"institution_name": "X", "csrf_token": "stale"}, headers={"Referer": "http://localhost/institutional"})
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].startswith("/institutional")


def test_csrf_failure_rejects_protocol_relative_referrer(client, app):
    register(client)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post("/account/profile", data={"username": "X Y", "csrf_token": "stale"}, headers={"Referer": "http://localhost//evil.com/x"})
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert not resp.headers["Location"].startswith("//")


def test_csrf_failure_without_referrer_does_not_target_post_only_url(client, app):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post("/papers/some-slug/upload-pdf", data={"csrf_token": "stale"})
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert resp.status_code in (302, 303)
    assert "upload-pdf" not in resp.headers["Location"]


def test_csrf_json_error_is_human_readable(client, app):
    register(client)
    paper_id, _ = _make_paper(app)
    model_id = _make_model(app, paper_id)
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        resp = client.post(f"/models/{model_id}/annotations", json={"label": "x"}, headers={"X-CSRFToken": "stale"})
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    body = resp.get_json()
    assert body["code"] == "csrf_expired"
    assert " " in body["error"]


# --- Polish ---------------------------------------------------------------------------------

def test_login_page_focuses_email(client):
    html = client.get("/auth/login").data.decode()
    assert re.search(r'name="email"[^>]*autofocus|autofocus[^>]*name="email"', html)


def test_institutional_page_uses_configured_contact_email(client, app):
    app.config["CONTACT_EMAIL"] = "sales@example.org"
    html = client.get("/institutional").data.decode()
    assert "sales@example.org" in html
    assert "hello@academicar.com" not in html


def test_public_page_pdf_button_finds_any_article_with_pdf(client, app):
    register(client)
    paper_id, slug = _make_paper(app)
    with app.app_context():
        db.session.add(ProjectArticle(project_id=paper_id, title="No PDF", order_index=0))
        db.session.add(ProjectArticle(project_id=paper_id, title="With PDF", pdf_path="pdfs/x.pdf", order_index=1))
        db.session.commit()
        pdf_article_id = ProjectArticle.query.filter_by(title="With PDF").first().id
    html = client.get(f"/p/{slug}").data.decode()
    assert "PDF not available" not in html
    assert f"/articles/{pdf_article_id}" in html


def test_article_rows_offer_doi_autofill(client):
    register(client)
    html = client.get("/papers/new").data.decode()
    assert "autofillBtn" not in html
    # Rendered row + the template used for "+ Article".
    assert html.count("data-article-autofill ") + html.count("data-article-autofill>") >= 2
    assert "/papers/fetch-metadata" in html


def test_expired_page_upgrade_buttons_open_the_plan_page_with_coupon_field(client, app):
    """Owners of an expired model choose a plan on the upgrade page (coupon
    field, plan details) instead of being sent straight to payment."""
    register(client)
    paper_id, _ = _make_paper(app)
    expired = datetime.now(UTC) - timedelta(days=1)
    model_id = _make_model(app, paper_id, license_type="free", expires=expired)
    html = client.get(f"/view/{model_id}").data.decode()
    assert f'/models/{model_id}/upgrade?plan=academic' in html
    assert f'action="/models/{model_id}/upgrade/' not in html
    assert "Have a coupon code?" in html
    page = client.get(f"/models/{model_id}/upgrade?plan=extended_archive").data.decode()
    assert 'name="coupon_code"' in page
    assert re.search(r'value="extended_archive"[^>]*checked', page)
