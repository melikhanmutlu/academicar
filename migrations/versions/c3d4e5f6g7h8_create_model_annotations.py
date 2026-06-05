"""create model_annotations table

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-06-05

Author-placed educational annotations (hotspot labels) on 3D models.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'c3d4e5f6g7h8'
down_revision = 'b2c3d4e5f6g7'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    bind = op.get_bind()
    insp = inspect(bind)
    return table_name in insp.get_table_names()


def upgrade():
    if not _table_exists('model_annotations'):
        op.create_table(
            'model_annotations',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('model_id', sa.String(36), sa.ForeignKey('models.id'), nullable=False),
            sa.Column('position_x', sa.Float(), nullable=False),
            sa.Column('position_y', sa.Float(), nullable=False),
            sa.Column('position_z', sa.Float(), nullable=False),
            sa.Column('normal_x', sa.Float(), nullable=False, server_default='0'),
            sa.Column('normal_y', sa.Float(), nullable=False, server_default='1'),
            sa.Column('normal_z', sa.Float(), nullable=False, server_default='0'),
            sa.Column('label', sa.String(120), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_model_annotations_model_id', 'model_annotations', ['model_id'])


def downgrade():
    if _table_exists('model_annotations'):
        op.drop_index('ix_model_annotations_model_id', table_name='model_annotations')
        op.drop_table('model_annotations')
