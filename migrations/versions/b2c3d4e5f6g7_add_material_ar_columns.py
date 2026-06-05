"""add roughness, metallic, ar_placement to models table

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-05

Material control (roughness/metallic) and AR placement choice.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'b2c3d4e5f6g7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    bind = op.get_bind()
    insp = inspect(bind)
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    with op.batch_alter_table('models', schema=None) as batch_op:
        if not _column_exists('models', 'appearance_roughness'):
            batch_op.add_column(sa.Column('appearance_roughness', sa.Float(), nullable=True, server_default='0.45'))
        if not _column_exists('models', 'appearance_metallic'):
            batch_op.add_column(sa.Column('appearance_metallic', sa.Float(), nullable=True, server_default='0.05'))
        if not _column_exists('models', 'ar_placement'):
            batch_op.add_column(sa.Column('ar_placement', sa.String(10), nullable=True, server_default='floor'))


def downgrade():
    with op.batch_alter_table('models', schema=None) as batch_op:
        if _column_exists('models', 'ar_placement'):
            batch_op.drop_column('ar_placement')
        if _column_exists('models', 'appearance_metallic'):
            batch_op.drop_column('appearance_metallic')
        if _column_exists('models', 'appearance_roughness'):
            batch_op.drop_column('appearance_roughness')
