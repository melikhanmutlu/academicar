"""add collage_path to models table

Revision ID: b1c2d3e4f5a6
Revises: f9a0b1c2d3e4
Create Date: 2026-07-19

Stores the path to a user-composed "combined view" figure (several viewer
screenshots, optionally with a QR badge, merged client-side into one image).
Fresh deploys get the column from db.create_all(); this migration backfills
existing PostgreSQL databases.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'b1c2d3e4f5a6'
down_revision = 'f9a0b1c2d3e4'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    bind = op.get_bind()
    insp = inspect(bind)
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    if not _column_exists('models', 'collage_path'):
        with op.batch_alter_table('models', schema=None) as batch_op:
            batch_op.add_column(sa.Column('collage_path', sa.String(500), nullable=True))


def downgrade():
    if _column_exists('models', 'collage_path'):
        with op.batch_alter_table('models', schema=None) as batch_op:
            batch_op.drop_column('collage_path')
