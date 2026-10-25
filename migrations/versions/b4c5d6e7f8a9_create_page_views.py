"""create page_views for the admin Traffic page

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-10-25

Cookieless site traffic (traffic.py). Idempotent like the rest of the repo:
db.create_all() may already have created the table before this runs.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

_INDEXED_COLUMNS = ("occurred_at", "path", "visitor_hash")


def _table_exists(table_name):
    return table_name in inspect(op.get_bind()).get_table_names()


def upgrade():
    if not _table_exists("page_views"):
        op.create_table(
            "page_views",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("path", sa.String(length=255), nullable=False),
            sa.Column("section", sa.String(length=32), nullable=True),
            sa.Column("visitor_hash", sa.String(length=64), nullable=False),
            sa.Column("is_entry", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("channel", sa.String(length=24), nullable=True),
            sa.Column("source", sa.String(length=120), nullable=True),
            sa.Column("referrer_domain", sa.String(length=255), nullable=True),
            sa.Column("utm_source", sa.String(length=120), nullable=True),
            sa.Column("utm_medium", sa.String(length=120), nullable=True),
            sa.Column("utm_campaign", sa.String(length=120), nullable=True),
            sa.Column("country_code", sa.String(length=2), nullable=True),
            sa.Column("device_type", sa.String(length=16), nullable=True),
            sa.Column("signed_in", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    for column in _INDEXED_COLUMNS:
        op.create_index(f"ix_page_views_{column}", "page_views", [column], if_not_exists=True)


def downgrade():
    for column in _INDEXED_COLUMNS:
        op.drop_index(f"ix_page_views_{column}", table_name="page_views", if_exists=True)
    if _table_exists("page_views"):
        op.drop_table("page_views")
