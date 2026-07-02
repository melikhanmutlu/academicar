"""add deactivated_at to users table

Revision ID: a1c2e3f4b5d6
Revises: d9e0f1a2b3c4
Create Date: 2026-07-02

Adds a nullable timestamp used by the admin panel to deactivate an account.
A deactivated user cannot log in and existing sessions are invalidated
(see load_user). NULL means the account is active.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'a1c2e3f4b5d6'
down_revision = 'd9e0f1a2b3c4'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    bind = op.get_bind()
    insp = inspect(bind)
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    if not _column_exists('users', 'deactivated_at'):
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.add_column(sa.Column('deactivated_at', sa.DateTime(), nullable=True))


def downgrade():
    if _column_exists('users', 'deactivated_at'):
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.drop_column('deactivated_at')
