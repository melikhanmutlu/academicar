"""models.layer_info + wider source_format (STEP and medical inputs)

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-10-05

layer_info holds the viewer layers the worker found (STEP assembly parts,
segmented structures, multi-part meshes). source_format grows to 20 chars for
"segmentation". Idempotent: columns are added / widened only when needed.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def _columns(table_name):
    insp = inspect(op.get_bind())
    if table_name not in insp.get_table_names():
        return None  # create_all() builds the table
    return {col["name"]: col for col in insp.get_columns(table_name)}


def _widen_source_format(table_name):
    columns = _columns(table_name)
    if not columns or "source_format" not in columns:
        return
    length = getattr(columns["source_format"]["type"], "length", None)
    # SQLite does not enforce VARCHAR lengths; only alter real databases.
    if op.get_bind().dialect.name == "sqlite" or (length is not None and length >= 20):
        return
    op.alter_column(table_name, "source_format", type_=sa.String(20), existing_type=sa.String(length or 10))


def upgrade():
    columns = _columns("models")
    if columns is not None and "layer_info" not in columns:
        with op.batch_alter_table("models", schema=None) as batch_op:
            batch_op.add_column(sa.Column("layer_info", sa.JSON(), nullable=True))
    _widen_source_format("models")
    _widen_source_format("model_versions")


def downgrade():
    columns = _columns("models")
    if columns is not None and "layer_info" in columns:
        with op.batch_alter_table("models", schema=None) as batch_op:
            batch_op.drop_column("layer_info")
