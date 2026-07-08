"""add slug and last_usage_report_at to institutions

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-08

Public showcase pages (/i/<slug>) and monthly usage report emails. Fresh
databases get the full institutions schema from db.create_all(); this
migration only patches databases that already materialized the table from
an earlier revision of the institutional module.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'f8a9b0c1d2e3'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    return inspect(op.get_bind()).has_table(table_name)


def _column_exists(table_name, column_name):
    insp = inspect(op.get_bind())
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    if not _table_exists('institutions'):
        # Table will be created complete by db.create_all() at startup.
        return
    if not _column_exists('institutions', 'slug'):
        with op.batch_alter_table('institutions', schema=None) as batch_op:
            batch_op.add_column(sa.Column('slug', sa.String(220), nullable=True))
    op.create_index('ix_institutions_slug', 'institutions', ['slug'],
                    unique=True, if_not_exists=True)
    if not _column_exists('institutions', 'last_usage_report_at'):
        with op.batch_alter_table('institutions', schema=None) as batch_op:
            batch_op.add_column(sa.Column('last_usage_report_at', sa.DateTime(), nullable=True))


def downgrade():
    if not _table_exists('institutions'):
        return
    op.drop_index('ix_institutions_slug', table_name='institutions')
    if _column_exists('institutions', 'last_usage_report_at'):
        with op.batch_alter_table('institutions', schema=None) as batch_op:
            batch_op.drop_column('last_usage_report_at')
    if _column_exists('institutions', 'slug'):
        with op.batch_alter_table('institutions', schema=None) as batch_op:
            batch_op.drop_column('slug')
