"""models.viewer_lighting and the model_media table

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-10-06

viewer_lighting keeps the owner's saved lighting preset for the viewer;
model_media keeps the images and videos captured or auto-generated for a
model. Idempotent: create_all() builds both on fresh databases.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    insp = inspect(op.get_bind())
    tables = insp.get_table_names()
    if "models" in tables and "viewer_lighting" not in {c["name"] for c in insp.get_columns("models")}:
        with op.batch_alter_table("models", schema=None) as batch_op:
            batch_op.add_column(sa.Column("viewer_lighting", sa.JSON(), nullable=True))
    if "models" in tables and "model_media" not in tables:
        op.create_table(
            "model_media",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("model_id", sa.String(length=36), sa.ForeignKey("models.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(length=10), nullable=False),
            sa.Column("source", sa.String(length=10), nullable=False),
            sa.Column("label", sa.String(length=120), nullable=True),
            sa.Column("filename", sa.String(length=120), nullable=False),
            sa.Column("mimetype", sa.String(length=60), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_model_media_model_id", "model_media", ["model_id"])


def downgrade():
    insp = inspect(op.get_bind())
    tables = insp.get_table_names()
    if "model_media" in tables:
        op.drop_index("ix_model_media_model_id", table_name="model_media")
        op.drop_table("model_media")
    if "models" in tables and "viewer_lighting" in {c["name"] for c in insp.get_columns("models")}:
        with op.batch_alter_table("models", schema=None) as batch_op:
            batch_op.drop_column("viewer_lighting")
