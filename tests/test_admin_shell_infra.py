"""Shared admin shell/infrastructure: per-page query budget, sidebar badge on
detail pages, AR doctor sidebar entry, literal LIKE search, index migration."""
import csv
import io
import os
from datetime import UTC, datetime

import pytest
from alembic.script import ScriptDirectory
from sqlalchemy import event, inspect

from app import admin_like_pattern
from models import (
    AuditLog,
    ConversionJob,
    Model3D,
    Paper,
    Payment,
    QRLink,
    User,
    db,
)
from tests.conftest import create_user, login

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")

LIST_PAGES = [
    "/admin/users",
    "/admin/content",
    "/admin/models",
    "/admin/jobs",
    "/admin/annotations",
    "/admin/access",
    "/admin/revenue",
    "/admin/pricing",
    "/admin/security",
    "/admin/storage",
    "/admin/system",
    "/admin/logs",
    "/admin/backups",
    "/admin/blog",
    "/admin/institutions",
]


def _seed(client):
    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin User")
        admin.is_admin = True
        member = create_user(email="member@example.com", username="Member")
        db.session.flush()
        paper = Paper(title="Seed Paper", slug="seed-paper", user_id=member.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="seed-model", paper_id=paper.id, user_id=member.id,
            glb_path="model.glb", processing_status="ready", file_size=1000,
        )
        db.session.add(model)
        db.session.flush()
        db.session.add(QRLink(public_id="seedqr", model_id=model.id))
        db.session.add(ConversionJob(job_type="model_upload", status="failed", model_id=model.id, attempts=1))
        db.session.add(ConversionJob(job_type="model_upload", status="pending", model_id=model.id, attempts=0))
        db.session.add(Payment(
            user_id=member.id, paper_id=paper.id, model_id=model.id, plan_key="academic",
            amount_kurus=990, currency="TRY", provider="manual", provider_reference="seed-pay",
            status="paid", paid_at=datetime.now(UTC),
        ))
        for _ in range(3):
            db.session.add(AuditLog(event_type="user_login", user_id=member.id, resource_id="x"))
        db.session.commit()
        return {"member_id": member.id, "model_id": model.id}


class QueryCounter:
    def __init__(self, engine):
        self.engine = engine
        self.count = 0

    def _on_execute(self, *args, **kwargs):
        self.count += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._on_execute)


@pytest.fixture()
def admin_client(client):
    ids = _seed(client)
    login(client, email="admin@example.com")
    client.ids = ids
    return client


@pytest.mark.parametrize("path", LIST_PAGES)
def test_admin_list_page_query_budget(admin_client, path):
    app = admin_client.application
    admin_client.get(path)  # warm caches (file scan, seeds) so the budget measures steady state
    with app.app_context():
        with QueryCounter(db.engine) as counter:
            response = admin_client.get(path)
    assert response.status_code == 200
    assert counter.count <= 20, f"{path} ran {counter.count} queries"


def test_admin_overview_query_budget_and_alerts(admin_client):
    admin_client.get("/admin")
    with admin_client.application.app_context():
        with QueryCounter(db.engine) as counter:
            response = admin_client.get("/admin")
    assert response.status_code == 200
    assert counter.count <= 30, f"overview ran {counter.count} queries"
    # critical_alerts still computed on the overview (failed job seeded).
    assert "failed conversion job" in response.get_data(as_text=True)


def test_sidebar_badge_shows_on_detail_pages(admin_client):
    ids = admin_client.ids
    for path in (
        f"/admin/users/{ids['member_id']}",
        f"/admin/models/{ids['model_id']}",
        "/admin/users",
    ):
        response = admin_client.get(path)
        assert response.status_code == 200
        text = response.get_data(as_text=True)
        assert 'class="admin-sidenav-alert" title="Failed jobs">1</span>' in text, path


def test_ar_doctor_not_in_sidebar_but_route_redirects(admin_client):
    page = admin_client.get("/admin/users").get_data(as_text=True)
    assert "AR doctor" not in page
    assert "ar-doctor" not in page
    response = admin_client.get("/admin/ar-doctor")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/system#ar-doctor")


def test_admin_like_pattern_escapes_wildcards():
    assert admin_like_pattern("50%_Off") == "%50\\%\\_off%"
    assert admin_like_pattern("a\\b") == "%a\\\\b%"


def test_admin_user_search_treats_percent_and_underscore_literally(admin_client):
    with admin_client.application.app_context():
        create_user(email="plain@example.com", username="Plain")
        create_user(email="100%_real@example.com", username="Percent")
    html = admin_client.get("/admin/users?user_q=%25").get_data(as_text=True)
    assert "100%_real@example.com" in html
    assert "plain@example.com" not in html
    html = admin_client.get("/admin/users?user_q=_").get_data(as_text=True)
    assert "100%_real@example.com" in html
    assert "plain@example.com" not in html
    # Unescaped, "p_ain" would match "plain"; it must not.
    html = admin_client.get("/admin/users?user_q=p_ain").get_data(as_text=True)
    assert "plain@example.com" not in html


def test_admin_csv_exports_use_literal_search(admin_client):
    with admin_client.application.app_context():
        create_user(email="plain@example.com", username="Plain")
        create_user(email="100%_real@example.com", username="Percent")
        db.session.add(AuditLog(event_type="odd_100%_event", resource_id="r"))
        db.session.commit()
    body = admin_client.get("/admin/users/export.csv?user_q=%25").get_data(as_text=True)
    emails = [row["email"] for row in csv.DictReader(io.StringIO(body))]
    assert emails == ["100%_real@example.com"]
    body = admin_client.get("/admin/logs/export.csv?audit_q=%25").get_data(as_text=True)
    events = [row["event_type"] for row in csv.DictReader(io.StringIO(body))]
    assert events == ["odd_100%_event"]
    assert admin_client.get("/admin/content/export.csv?paper_q=%25").status_code == 200
    assert admin_client.get("/admin/revenue/export.csv?pay_q=%25").status_code == 200


def test_migrations_single_head_and_index_migration_is_head():
    script = ScriptDirectory(MIGRATIONS_DIR)
    heads = script.get_heads()
    assert len(heads) == 1
    # The index migration is on the single chain (later data migrations may follow it).
    assert "a1d2e3f4b5c6" in {rev.revision for rev in script.iterate_revisions(heads[0], "base")}


def test_create_all_declares_admin_indexes(app):
    with app.app_context():
        inspector = inspect(db.engine)
        audit = {ix["name"] for ix in inspector.get_indexes("audit_logs")}
        analytics = {ix["name"] for ix in inspector.get_indexes("analytics_events")}
    assert {"ix_audit_logs_event_type_timestamp", "ix_audit_logs_user_id"} <= audit
    assert "ix_analytics_events_event_name_occurred_at" in analytics


def test_index_migration_is_idempotent_on_existing_indexes(app):
    """The migration runs against a DB whose indexes already exist (created by
    create_all) without error, and again after dropping them."""
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = os.path.join(MIGRATIONS_DIR, "versions", "a1d2e3f4b5c6_admin_log_and_analytics_indexes.py")
    spec = importlib.util.spec_from_file_location("admin_index_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with app.app_context():
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                module.upgrade()
                module.downgrade()
                module.upgrade()
        names = {ix["name"] for ix in inspect(db.engine).get_indexes("audit_logs")}
    assert "ix_audit_logs_event_type_timestamp" in names


def test_sparkline_and_flash_css_present():
    root = os.path.dirname(os.path.dirname(__file__))
    style = open(os.path.join(root, "static", "css", "style.css"), encoding="utf-8").read()
    admin = open(os.path.join(root, "static", "css", "admin.css"), encoding="utf-8").read()
    assert ".admin-breakdown .admin-sparkline" in style
    assert "body:has(.admin-layout) .flash-stack" in admin
    for utility in (".admin-grow", ".admin-wrap-row", ".admin-mt", ".admin-inline", ".admin-field-narrow", ".admin-hide-sm"):
        assert utility in admin
    template = open(os.path.join(root, "templates", "admin", "analytics.html"), encoding="utf-8").read()
    assert "<style>" not in template


def test_closed_drawer_hidden_from_tab_and_scroll_locked_in_css():
    root = os.path.dirname(os.path.dirname(__file__))
    admin = open(os.path.join(root, "static", "css", "admin.css"), encoding="utf-8").read()
    assert "visibility: hidden" in admin
    assert "body.admin-drawer-open" in admin
    base = open(os.path.join(root, "templates", "admin", "base.html"), encoding="utf-8").read()
    assert "admin-drawer-open" in base and "Escape" in base
