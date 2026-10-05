"""model_comparisons table (before/after links)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-10-05

Idempotent: create_all() may already have built the table at startup.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade():
    if "model_comparisons" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "model_comparisons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(40), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("left_model_id", sa.String(36), sa.ForeignKey("models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("right_model_id", sa.String(36), sa.ForeignKey("models.id", ondelete="CASCADE"), nullable=False),
        sa.Column("left_label", sa.String(60), nullable=True),
        sa.Column("right_label", sa.String(60), nullable=True),
        sa.Column("sync_camera", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_model_comparisons_public_id", "model_comparisons", ["public_id"], unique=True)
    op.create_index("ix_model_comparisons_owner_user_id", "model_comparisons", ["owner_user_id"])
    op.create_index("ix_model_comparisons_left_model_id", "model_comparisons", ["left_model_id"])
    op.create_index("ix_model_comparisons_right_model_id", "model_comparisons", ["right_model_id"])


def downgrade():
    if "model_comparisons" in inspect(op.get_bind()).get_table_names():
        op.drop_table("model_comparisons")
