"""add plan_key to payments table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-06

Stores the license plan a payment grants so gateways that don't echo custom data
in their callback (e.g. PayTR) can recover it from the Payment row.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    bind = op.get_bind()
    insp = inspect(bind)
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    if not _column_exists('payments', 'plan_key'):
        with op.batch_alter_table('payments', schema=None) as batch_op:
            batch_op.add_column(sa.Column('plan_key', sa.String(30), nullable=True))


def downgrade():
    if _column_exists('payments', 'plan_key'):
        with op.batch_alter_table('payments', schema=None) as batch_op:
            batch_op.drop_column('plan_key')
