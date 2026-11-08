"""project_attachments.model_slides

Revision ID: dfb775c05ea9
Revises: b4c5d6e7f8a9
Create Date: 2026-11-08

Presenter mode places live 3D model slides between a presentation's pages:
[{"after": <page number, 0 = before the first page>, "model_id": "<uuid>"}].
Idempotent: added only if missing.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "dfb775c05ea9"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    insp = inspect(op.get_bind())
    if table_name not in insp.get_table_names():
        return True  # nothing to alter; create_all() builds the table
    return column_name in [col["name"] for col in insp.get_columns(table_name)]


def upgrade():
    if not _column_exists("project_attachments", "model_slides"):
        with op.batch_alter_table("project_attachments", schema=None) as batch_op:
            batch_op.add_column(sa.Column("model_slides", sa.JSON(), nullable=True))


def downgrade():
    insp = inspect(op.get_bind())
    if "project_attachments" in insp.get_table_names() and "model_slides" in [
        c["name"] for c in insp.get_columns("project_attachments")
    ]:
        with op.batch_alter_table("project_attachments", schema=None) as batch_op:
            batch_op.drop_column("model_slides")
