"""users.email_verified_at

Revision ID: e2f3a4b5c6d7
Revises: d9e8f7a6b5c4
Create Date: 2026-10-04

Password sign-ups now confirm their email address. Accounts that existed
before this change are treated as verified (backfilled from created_at) so no
current user is suddenly asked to verify or loses access to anything.

Idempotent: the column is added only if missing; the backfill runs only in
the same step that adds it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "e2f3a4b5c6d7"
down_revision = "d9e8f7a6b5c4"
branch_labels = None
depends_on = None


def _table_exists(table_name):
    return table_name in inspect(op.get_bind()).get_table_names()


def _column_exists(table_name, column_name):
    insp = inspect(op.get_bind())
    return column_name in [col["name"] for col in insp.get_columns(table_name)]


def upgrade():
    if _table_exists("users") and not _column_exists("users", "email_verified_at"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(sa.Column("email_verified_at", sa.DateTime(), nullable=True))
        op.execute("UPDATE users SET email_verified_at = COALESCE(created_at, CURRENT_TIMESTAMP) WHERE email_verified_at IS NULL")


def downgrade():
    if _table_exists("users") and _column_exists("users", "email_verified_at"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("email_verified_at")
