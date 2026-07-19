"""create privacy-minimised analytics events

Revision ID: 58e532f1a9b0
Revises: f8a9b0c1d2e3
Create Date: 2026-07-19

Local SQLite / fresh deploys get this table from db.create_all(); existing
PostgreSQL databases need this migration. Idempotent to match the rest of the
repo: db.create_all() runs inside create_app() before every ``flask db
upgrade``, so the table and its indexes may already exist by the time this
runs — guard the create_table and use if_not_exists on the indexes so it is a
no-op in that case (previously this failed with "relation already exists").
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "58e532f1a9b0"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None

_INDEXED_COLUMNS = (
    "event_name", "owner_user_id", "actor_user_id", "project_id", "model_id",
    "visitor_hash", "session_hash", "country_code", "occurred_at",
)


def _table_exists(table_name):
    bind = op.get_bind()
    insp = inspect(bind)
    return table_name in insp.get_table_names()


def upgrade():
    if not _table_exists("analytics_events"):
        op.create_table(
            "analytics_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_name", sa.String(length=64), nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=True),
            sa.Column("actor_user_id", sa.Integer(), nullable=True),
            sa.Column("project_id", sa.Integer(), nullable=True),
            sa.Column("model_id", sa.String(length=36), nullable=True),
            sa.Column("visitor_hash", sa.String(length=64), nullable=True),
            sa.Column("session_hash", sa.String(length=64), nullable=True),
            sa.Column("country_code", sa.String(length=2), nullable=True),
            sa.Column("device_type", sa.String(length=16), nullable=True),
            sa.Column("referrer_domain", sa.String(length=255), nullable=True),
            sa.Column("utm_source", sa.String(length=120), nullable=True),
            sa.Column("utm_medium", sa.String(length=120), nullable=True),
            sa.Column("utm_campaign", sa.String(length=120), nullable=True),
            sa.Column("properties", sa.JSON(), nullable=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["project_id"], ["papers.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="SET NULL"),
        )
    for column in _INDEXED_COLUMNS:
        op.create_index(
            f"ix_analytics_events_{column}", "analytics_events", [column],
            if_not_exists=True,
        )


def downgrade():
    for column in _INDEXED_COLUMNS:
        op.drop_index(f"ix_analytics_events_{column}", table_name="analytics_events", if_exists=True)
    if _table_exists("analytics_events"):
        op.drop_table("analytics_events")
