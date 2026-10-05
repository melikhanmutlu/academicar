"""models.processing_progress / processing_stage (live conversion progress)

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-10-05

The worker writes a 0-100 percentage and a short stage label while a model
converts, so the project page can show real progress. Idempotent: columns are
added only when missing (create_all() builds them on fresh databases).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "e8f9a0b1c2d3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def _model_columns():
    insp = inspect(op.get_bind())
    if "models" not in insp.get_table_names():
        return None  # create_all() builds the table
    return {col["name"] for col in insp.get_columns("models")}


def upgrade():
    columns = _model_columns()
    if columns is None:
        return
    with op.batch_alter_table("models", schema=None) as batch_op:
        if "processing_progress" not in columns:
            batch_op.add_column(sa.Column("processing_progress", sa.Integer(), nullable=True))
        if "processing_stage" not in columns:
            batch_op.add_column(sa.Column("processing_stage", sa.String(length=120), nullable=True))


def downgrade():
    columns = _model_columns()
    if columns is None:
        return
    with op.batch_alter_table("models", schema=None) as batch_op:
        if "processing_stage" in columns:
            batch_op.drop_column("processing_stage")
        if "processing_progress" in columns:
            batch_op.drop_column("processing_progress")
