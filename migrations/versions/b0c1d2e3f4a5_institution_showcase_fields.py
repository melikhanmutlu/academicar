"""add showcase branding and renewal-reminder fields to institutions

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-07-08

Showcase logo/description and the contract renewal-reminder stamp. Fresh
databases get these from db.create_all(); this migration patches databases
whose institutions table predates the columns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'b0c1d2e3f4a5'
down_revision = 'a9b0c1d2e3f4'
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
        return
    additions = {
        'logo_path': sa.Column('logo_path', sa.String(500), nullable=True),
        'public_description': sa.Column('public_description', sa.Text(), nullable=True),
        'renewal_reminder_sent_for': sa.Column('renewal_reminder_sent_for', sa.DateTime(), nullable=True),
    }
    for name, column in additions.items():
        if not _column_exists('institutions', name):
            with op.batch_alter_table('institutions', schema=None) as batch_op:
                batch_op.add_column(column)


def downgrade():
    if not _table_exists('institutions'):
        return
    for name in ('renewal_reminder_sent_for', 'public_description', 'logo_path'):
        if _column_exists('institutions', name):
            with op.batch_alter_table('institutions', schema=None) as batch_op:
                batch_op.drop_column(name)
