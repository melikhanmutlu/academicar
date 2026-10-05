"""model_scenes table (saved views with their own link and optional AR variant)

Revision ID: c6d7e8f9a0b1
Revises: b6c7d8e9f0a1
Create Date: 2026-10-05

Idempotent: create_all() may already have built the table at startup.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "c6d7e8f9a0b1"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade():
    if "model_scenes" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "model_scenes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("public_id", sa.String(40), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("ar_status", sa.String(20), nullable=False, server_default="none"),
        sa.Column("ar_glb_path", sa.String(500), nullable=True),
        sa.Column("ar_usdz_path", sa.String(500), nullable=True),
        sa.Column("ar_file_size", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_scenes_model_id", "model_scenes", ["model_id"])
    op.create_index("ix_model_scenes_public_id", "model_scenes", ["public_id"], unique=True)


def downgrade():
    if "model_scenes" in inspect(op.get_bind()).get_table_names():
        op.drop_table("model_scenes")
