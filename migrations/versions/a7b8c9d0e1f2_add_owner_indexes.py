"""add owner/foreign-key indexes for hot lookup paths

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-08 22:00:00.000000

models.user_id, models.paper_id and papers.user_id are filtered on every
dashboard / "my models" / paper-detail query but had no index, forcing a full
table scan as the data grows. These indexes are also declared on the
SQLAlchemy models (index=True) so fresh deployments materialised via
db.create_all() pick them up; this migration backfills existing databases.

Idempotent: every create_index uses if_not_exists so re-running (or running on
a database that already has the index from create_all) is a no-op.
"""
from alembic import op


revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_models_user_id', 'models', ['user_id'],
                    unique=False, if_not_exists=True)
    op.create_index('ix_models_paper_id', 'models', ['paper_id'],
                    unique=False, if_not_exists=True)
    op.create_index('ix_papers_user_id', 'papers', ['user_id'],
                    unique=False, if_not_exists=True)


def downgrade():
    op.drop_index('ix_papers_user_id', table_name='papers', if_exists=True)
    op.drop_index('ix_models_paper_id', table_name='models', if_exists=True)
    op.drop_index('ix_models_user_id', table_name='models', if_exists=True)
