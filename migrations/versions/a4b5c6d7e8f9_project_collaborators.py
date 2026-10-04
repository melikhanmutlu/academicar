"""project_collaborators table + models.uploaded_by_user_id

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-10-04

Co-authors / lab members can edit a project's content. Idempotent: the
table and column are created only if missing (create_all() may already
have built the table at startup).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "a4b5c6d7e8f9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def _tables():
    return inspect(op.get_bind()).get_table_names()


def _columns(table):
    return [col["name"] for col in inspect(op.get_bind()).get_columns(table)]


def upgrade():
    if "project_collaborators" not in _tables():
        op.create_table(
            "project_collaborators",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
            sa.Column("email", sa.String(120), nullable=False),
            sa.Column("invited_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("accepted_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("paper_id", "email", name="uq_project_collaborator_email"),
        )
        op.create_index("ix_project_collaborators_paper_id", "project_collaborators", ["paper_id"])
        op.create_index("ix_project_collaborators_user_id", "project_collaborators", ["user_id"])
        op.create_index("ix_project_collaborators_email", "project_collaborators", ["email"])
    if "models" in _tables() and "uploaded_by_user_id" not in _columns("models"):
        with op.batch_alter_table("models", schema=None) as batch_op:
            batch_op.add_column(sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True))


def downgrade():
    if "models" in _tables() and "uploaded_by_user_id" in _columns("models"):
        with op.batch_alter_table("models", schema=None) as batch_op:
            batch_op.drop_column("uploaded_by_user_id")
    if "project_collaborators" in _tables():
        op.drop_table("project_collaborators")
