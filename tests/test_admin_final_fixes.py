"""Regression tests for the FINAL CHECK findings of the admin panel.

1. stored XSS through ``|tojson`` inside a double-quoted ``onsubmit`` attribute
2. ``/admin/payments/create`` 500 on nan / inf / 1e400 amounts
3. appearance save claiming success when the GLB could not be rewritten
4. ``/admin/institutions/<id>`` N+1 because the audit commit expired the rows
5. "Regenerate QR" silently re-activating an admin-disabled QR link
6. mobile-only ``<small>`` lines leaking onto desktop (CSS specificity)
7. small admin template / CSS consistency fixes
"""
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event

from models import AuditLog, Institution, InstitutionInvite, InstitutionMember, Model3D, Paper, Payment, QRLink, User, db
from tests.conftest import create_user, login
from tests.test_admin_enrichment import _make_admin, _seed_annotation, _seed_model

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
ADMIN_CSS = (ROOT / "static" / "css" / "admin.css").read_text(encoding="utf-8")


# --- 1. stored XSS -----------------------------------------------------------


def test_annotation_label_cannot_inject_attributes_into_delete_form(client):
    _make_admin(client)
    model_id = _seed_model(client)
    _seed_annotation(client, model_id, label="x onmouseover=alert(1) y")
    html = client.get("/admin/annotations").get_data(as_text=True)

    class Forms(HTMLParser):
        def __init__(self):
            super().__init__()
            self.forms = []

        def handle_starttag(self, tag, attrs):
            if tag == "form":
                self.forms.append(dict(attrs))

    parser = Forms()
    parser.feed(html)
    delete_forms = [f for f in parser.forms if "/annotations/" in f.get("action", "") and f["action"].endswith("/delete")]
    assert delete_forms, html
    for attrs in delete_forms:
        # The label never becomes an attribute of its own; it stays inside the
        # confirm() string (tojson escapes ' < > & so a single-quoted attribute is safe).
        assert set(attrs) == {"method", "action", "onsubmit"}, attrs
        assert attrs["onsubmit"].startswith("return confirm(\"Delete annotation"), attrs["onsubmit"]
        assert "x onmouseover=alert(1) y" in attrs["onsubmit"]
    raw_tag = re.search(r"<form[^>]*annotations/\d+/delete[^>]*>", html).group(0)
    assert "onsubmit='return confirm(" in raw_tag, raw_tag


def test_no_template_puts_tojson_inside_a_double_quoted_attribute():
    """``|tojson`` output starts with a double quote, so it can only be used in
    a single-quoted HTML attribute (or outside attributes, e.g. in <script>)."""
    pattern = re.compile(r'=\s*"[^"\n]*\{\{[^}]*\|\s*tojson')
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == []


# --- 2. manual payment amount parsing ----------------------------------------


def _post_payment(client, **overrides):
    data = {"user_email": "owner@example.com", "amount": "10", "currency": "TRY", "status": "pending"}
    data.update(overrides)
    return client.post("/admin/payments/create", data=data, follow_redirects=True)


def test_manual_payment_rejects_non_finite_amounts(client):
    _make_admin(client)
    _seed_model(client)
    for raw in ("nan", "inf", "-inf", "1e400", "abc", ""):
        response = _post_payment(client, amount=raw)
        assert response.status_code == 200, raw
        assert "Amount must be a number." in response.get_data(as_text=True), raw
    response = _post_payment(client, amount="100000000")
    assert "Amount cannot exceed" in response.get_data(as_text=True)
    with client.application.app_context():
        assert Payment.query.count() == 0


def test_manual_payment_validates_currency_code(client):
    _make_admin(client)
    _seed_model(client)
    response = _post_payment(client, currency="$$$")
    assert "Currency must be a 3-letter code" in response.get_data(as_text=True)
    response = _post_payment(client, currency=" usd ")
    assert "Manual payment recorded." in response.get_data(as_text=True)
    with client.application.app_context():
        assert [p.currency for p in Payment.query.all()] == ["USD"]


# --- 3. truthful appearance save --------------------------------------------


def test_appearance_colour_is_not_persisted_when_glb_is_missing(client):
    _make_admin(client)
    missing = os.path.join(tempfile.mkdtemp(), "model.glb")
    model_id = _seed_model(client, glb_path=missing, appearance_color="#cccccc")
    with patch("app.mirror_file"), patch("app.ensure_local"):
        response = client.post(
            f"/admin/models/{model_id}/appearance",
            data={"color": "#336699", "roughness": "0.6", "metallic": "0.1", "ar_placement": "wall"},
            follow_redirects=True,
        )
    text = response.get_data(as_text=True)
    assert "Changes saved." not in text
    assert "could not be updated" in text
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.appearance_color == "#cccccc"
        assert model.ar_placement != "wall"
        assert AuditLog.query.filter_by(event_type="admin_model_appearance_changed").count() == 0


def test_appearance_name_only_save_works_without_glb(client):
    _make_admin(client)
    missing = os.path.join(tempfile.mkdtemp(), "model.glb")
    model_id = _seed_model(client, glb_path=missing)
    with patch("app.mirror_file"), patch("app.ensure_local"):
        response = client.post(
            f"/admin/models/{model_id}/appearance",
            data={"color_changed": "0", "finish_changed": "0", "display_name": "Renamed", "ar_placement": "wall"},
            follow_redirects=True,
        )
    assert "Changes saved." in response.get_data(as_text=True)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.display_name == "Renamed" and model.ar_placement == "wall"


def test_owner_appearance_colour_is_not_persisted_when_glb_is_missing(client):
    with client.application.app_context():
        owner = create_user(email="owner@example.com", username="Owner")
        paper = Paper(title="P", slug="owner-paper", user_id=owner.id)
        db.session.add(paper)
        db.session.flush()
        missing = os.path.join(tempfile.mkdtemp(), "model.glb")
        model = Model3D(
            id="owner-model-1", paper_id=paper.id, user_id=owner.id, glb_path=missing,
            processing_status="ready", appearance_color="#cccccc",
            anonymization_confirmed=True, rights_confirmed=True, ethics_responsibility_confirmed=True,
        )
        db.session.add(model)
        db.session.commit()
    login(client, email="owner@example.com")
    with patch("app.mirror_file"), patch("app.ensure_local"):
        response = client.post(
            "/models/owner-model-1/appearance",
            data={"color": "#336699", "color_changed": "1", "finish_changed": "0"},
            follow_redirects=True,
        )
    text = response.get_data(as_text=True)
    assert "Changes saved." not in text and "could not be updated" in text
    with client.application.app_context():
        assert db.session.get(Model3D, "owner-model-1").appearance_color == "#cccccc"


# --- 4. institution detail query budget --------------------------------------


def _seed_institution(client, members=8):
    with client.application.app_context():
        now = datetime.now(UTC)
        institution = Institution(
            name="Query Uni", slug="query-uni", status="active",
            contract_starts_at=now - timedelta(days=1), contract_ends_at=now + timedelta(days=365),
        )
        db.session.add(institution)
        db.session.flush()
        for i in range(members):
            user = User(email=f"m{i}@example.com", username=f"M{i}")
            user.set_password("password123")
            db.session.add(user)
            db.session.flush()
            db.session.add(InstitutionMember(institution_id=institution.id, user_id=user.id, role="admin" if i == 0 else "member"))
            paper = Paper(title=f"IP{i}", slug=f"ip-{i}", user_id=user.id, status="active")
            db.session.add(paper)
            db.session.flush()
            db.session.add(Model3D(
                id=f"inst-model-{i}", paper_id=paper.id, user_id=user.id, glb_path="model.glb",
                processing_status="ready", license_type="institutional", institution_id=institution.id, file_size=10,
            ))
            db.session.add(InstitutionInvite(institution_id=institution.id, token=f"tok{i}"))
            db.session.add(Payment(institution_id=institution.id, amount_kurus=100, currency="TRY", provider="manual", status="paid"))
        db.session.commit()
        return institution.id


def _count_queries(client, url):
    with client.application.app_context():
        engine = db.engine
    n = [0]

    def hook(*_a, **_k):
        n[0] += 1

    event.listen(engine, "before_cursor_execute", hook)
    try:
        assert client.get(url).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", hook)
    return n[0]


def test_institution_detail_query_count_is_flat(client):
    _make_admin(client)
    institution_id = _seed_institution(client, members=8)
    queries = _count_queries(client, f"/admin/institutions/{institution_id}")
    assert queries < 20, queries
    with client.application.app_context():
        assert AuditLog.query.filter_by(event_type="admin_institution_detail_viewed").count() == 1
    html = client.get(f"/admin/institutions/{institution_id}").get_data(as_text=True)
    assert "m7@example.com" in html and "Institution admin" in html


# --- 5. QR regeneration keeps the link status --------------------------------


def test_regenerate_qr_keeps_disabled_link_disabled(client):
    _make_admin(client)
    model_id = _seed_model(client)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        model.public_id = "disabled-pub"
        db.session.add(QRLink(public_id="disabled-pub", model_id=model.id, status="disabled"))
        db.session.commit()
    response = client.post(f"/admin/models/{model_id}/qr/regenerate", data={"next": "access"}, follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        link = QRLink.query.filter_by(model_id=model_id).one()
        assert link.status == "disabled"
        assert link.public_id == "disabled-pub"
        assert db.session.get(Model3D, model_id).qr_code_path


def test_ensure_model_qr_link_still_creates_an_active_link(client):
    model_id = _seed_model(client)
    from app import ensure_model_qr_link

    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        link = ensure_model_qr_link(model)
        db.session.commit()
        assert link.status == "active" and link.public_id == model.public_id
        # Idempotent: a second call returns the same row, status untouched.
        link.status = "disabled"
        db.session.commit()
        again = ensure_model_qr_link(db.session.get(Model3D, model_id))
        assert again.id == link.id and again.status == "disabled"


# --- 6/7. CSS + template consistency -----------------------------------------


def test_mobile_only_small_lines_outrank_the_generic_table_rule():
    # .admin-table small {display:block} is (0,1,1); the hide rule must be at least that specific.
    assert ".admin-table small.admin-show-sm { display: none; }" in ADMIN_CSS
    assert ".admin-table small.revenue-amount-sm { display: none; }" in ADMIN_CSS
    assert ADMIN_CSS.count("small.admin-show-sm { display: none; }") == 1
    assert "\n.admin-show-sm { display: none; }" not in ADMIN_CSS
    assert "\n.revenue-amount-sm { display: none; }" not in ADMIN_CSS


def test_desktop_sidebar_breakpoint_excludes_1024px():
    assert "@media (max-width: 1024px)" not in ADMIN_CSS
    assert "@media (max-width: 1023px)" in ADMIN_CSS


def test_pricing_summary_pluralises_models_per_topic(client):
    _make_admin(client)
    html = client.get("/admin/pricing").get_data(as_text=True)
    assert "1 models/topic" not in html
    assert re.search(r"\b1 model/topic", html) or "models/topic" in html


def test_jobs_and_blog_counters_use_the_section_head_pattern(client):
    _make_admin(client)
    jobs = client.get("/admin/jobs").get_data(as_text=True)
    assert "admin-page-note" not in jobs
    assert re.search(r'<div class="admin-section-head">\s*<span>0 jobs</span>', jobs), jobs
    blog = client.get("/admin/blog").get_data(as_text=True)
    assert re.search(r'<div class="admin-section-head admin-mt">\s*<span>\d+ posts?</span>', blog), blog


def test_logs_default_filter_is_labelled_admin_actions(client):
    _make_admin(client)
    html = client.get("/admin/logs").get_data(as_text=True)
    assert 'value="actions" selected>Admin actions<' in html


def test_model_detail_license_tile_is_text_and_counter_is_grouped(client):
    _make_admin(client)
    model_id = _seed_model(client)
    html = client.get(f"/admin/models/{model_id}").get_data(as_text=True)
    assert re.search(r'<span>License</span>\s*<strong class="is-text">', html)
    assert "<span>Latest 10</span>" not in html
    assert "admin-metric strong .status-chip" in ADMIN_CSS
    assert ".admin-blog-new[open] > summary" in ADMIN_CSS
