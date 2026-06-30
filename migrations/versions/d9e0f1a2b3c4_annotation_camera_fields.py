"""add camera angle fields to model_annotations

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-06-30

Lets an annotation remember the camera angle it was placed from, so clicking the
hotspot can fly the camera back to that view. Columns are nullable (older rows and
fresh create_all() schemas stay valid).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'd9e0f1a2b3c4'
down_revision = 'c8d9e0f1a2b3'
branch_labels = None
depends_on = None


def _columns(table_name):
    bind = op.get_bind()
    insp = inspect(bind)
    if table_name not in insp.get_table_names():
        return set()
    return {col['name'] for col in insp.get_columns(table_name)}


def upgrade():
    existing = _columns('model_annotations')
    if not existing:
        return
    with op.batch_alter_table('model_annotations') as batch_op:
        if 'camera_orbit' not in existing:
            batch_op.add_column(sa.Column('camera_orbit', sa.String(64), nullable=True))
        if 'camera_target' not in existing:
            batch_op.add_column(sa.Column('camera_target', sa.String(96), nullable=True))
        if 'camera_fov' not in existing:
            batch_op.add_column(sa.Column('camera_fov', sa.String(16), nullable=True))


def downgrade():
    existing = _columns('model_annotations')
    if not existing:
        return
    with op.batch_alter_table('model_annotations') as batch_op:
        if 'camera_fov' in existing:
            batch_op.drop_column('camera_fov')
        if 'camera_target' in existing:
            batch_op.drop_column('camera_target')
        if 'camera_orbit' in existing:
            batch_op.drop_column('camera_orbit')
