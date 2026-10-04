"""users.impact_report_opt_out

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-04

Monthly impact report emails (views, QR scans, AR starts per model) are sent
by the worker; users can switch them off from their profile or the email's
unsubscribe link. Idempotent: added only if missing.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    insp = inspect(op.get_bind())
    if table_name not in insp.get_table_names():
        return True  # nothing to alter; create_all() builds the table
    return column_name in [col["name"] for col in insp.get_columns(table_name)]


def upgrade():
    if not _column_exists("users", "impact_report_opt_out"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("impact_report_opt_out", sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade():
    insp = inspect(op.get_bind())
    if "users" in insp.get_table_names() and "impact_report_opt_out" in [c["name"] for c in insp.get_columns("users")]:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("impact_report_opt_out")
