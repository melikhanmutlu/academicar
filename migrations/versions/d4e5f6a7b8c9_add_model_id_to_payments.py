"""add model_id to payments table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6g7h8
Create Date: 2026-06-06

A payment buys a license window for a single Model3D, so payments now carry a
nullable model_id foreign key (ondelete=SET NULL keeps the invoice record alive
if the model is deleted). Local SQLite already gets the column from
db.create_all(); PostgreSQL needs this migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6g7h8'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    bind = op.get_bind()
    insp = inspect(bind)
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    # Add model_id only when missing (local SQLite already has it from
    # db.create_all; PostgreSQL does not).
    if not _column_exists('payments', 'model_id'):
        with op.batch_alter_table('payments', schema=None) as batch_op:
            batch_op.add_column(sa.Column('model_id', sa.String(36), nullable=True))

    op.create_index('ix_payments_model_id', 'payments', ['model_id'],
                    unique=False, if_not_exists=True)


def downgrade():
    op.drop_index('ix_payments_model_id', table_name='payments')
    if _column_exists('payments', 'model_id'):
        with op.batch_alter_table('payments', schema=None) as batch_op:
            batch_op.drop_column('model_id')
