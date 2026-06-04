"""Add email_verified to users (backfilled True for existing accounts)

Existing accounts predate email verification, so they are backfilled to True —
the new flag must never lock anyone out of access they already had. New password
registrations start False and confirm via an emailed link.

Revision ID: c8a3d5e1f9b2
Revises: b7f2c1a9d4e3
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c8a3d5e1f9b2"
down_revision = "b7f2c1a9d4e3"
branch_labels = None
depends_on = None


def _has_column(bind, table, column):
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def upgrade():
    bind = op.get_bind()
    if _has_column(bind, "users", "email_verified"):
        return
    # Add nullable first so existing rows can be backfilled, then enforce NOT NULL.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email_verified", sa.Boolean(), nullable=True))
    op.execute("UPDATE users SET email_verified = 1 WHERE email_verified IS NULL")
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "email_verified",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        )


def downgrade():
    bind = op.get_bind()
    if not _has_column(bind, "users", "email_verified"):
        return
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verified")
