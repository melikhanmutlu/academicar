"""create license_plans table

Revision ID: b3c4d5e6f7a8
Revises: c5d6e7f8a9b0
Create Date: 2026-07-06

Admin-editable pricing/duration/storage facts per license plan key. Rows are
seeded at startup from licensing.py's Python defaults by app.seed_license_plans
(mirrors sync_configured_admins). This migration only creates the table; local
SQLite gets it from db.create_all() (same pattern as create_blog_posts).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = 'b3c4d5e6f7a8'
down_revision = 'c5d6e7f8a9b0'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    bind = op.get_bind()
    return table_name in inspect(bind).get_table_names()


def upgrade():
    if not _table_exists('license_plans'):
        op.create_table(
            'license_plans',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('key', sa.String(30), nullable=False),
            sa.Column('label', sa.String(80), nullable=False),
            sa.Column('price_usd_cents', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('duration_days', sa.Integer(), nullable=True),
            sa.Column('storage_limit_bytes', sa.Integer(), nullable=False),
            sa.Column('is_purchasable', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('key'),
        )
        op.create_index('ix_license_plans_key', 'license_plans', ['key'], unique=True)


def downgrade():
    if _table_exists('license_plans'):
        op.drop_index('ix_license_plans_key', table_name='license_plans')
        op.drop_table('license_plans')
