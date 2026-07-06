"""add r2_mirror_failed_at to models table

Revision ID: c5d6e7f8a9b0
Revises: a1c2e3f4b5d6
Create Date: 2026-07-06

Tracks a failed synchronous R2 mirror attempt (worker context) so the admin
Storage page can surface it and offer a retry, instead of the failure only
ever appearing in a raw server log line.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'c5d6e7f8a9b0'
down_revision = 'a1c2e3f4b5d6'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    bind = op.get_bind()
    insp = inspect(bind)
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    if not _column_exists('models', 'r2_mirror_failed_at'):
        with op.batch_alter_table('models', schema=None) as batch_op:
            batch_op.add_column(sa.Column('r2_mirror_failed_at', sa.DateTime(), nullable=True))


def downgrade():
    if _column_exists('models', 'r2_mirror_failed_at'):
        with op.batch_alter_table('models', schema=None) as batch_op:
            batch_op.drop_column('r2_mirror_failed_at')
