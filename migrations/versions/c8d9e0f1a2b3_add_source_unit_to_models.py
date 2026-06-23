"""Add source_unit to models (declared mm/cm/m, or "embedded" for FBX/GLB).

Fresh deploys get the column from db.create_all(); this incremental migration
adds it to already-migrated databases. Idempotent: skips if the column exists.
"""
from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in cols


def upgrade():
    if not _has_column("models", "source_unit"):
        op.add_column("models", sa.Column("source_unit", sa.String(length=12), nullable=True))


def downgrade():
    if _has_column("models", "source_unit"):
        op.drop_column("models", "source_unit")
