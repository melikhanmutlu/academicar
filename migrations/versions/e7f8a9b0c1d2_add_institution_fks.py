"""add institution_id to models and payments

Revision ID: e7f8a9b0c1d2
Revises: b3c4d5e6f7a8
Create Date: 2026-07-07

Institutional (B2B) support: models funded by an institution's contract and
payments recording that contract's invoices carry a nullable institution_id.
The new tables themselves (institutions, institution_members,
institution_invites) are NOT created here — per this repo's migration
architecture they materialize via db.create_all() at app startup; this
migration only adds the new columns to pre-existing tables (local SQLite
already gets them from create_all; PostgreSQL does not).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'e7f8a9b0c1d2'
down_revision = 'b3c4d5e6f7a8'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    bind = op.get_bind()
    insp = inspect(bind)
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    if not _column_exists('models', 'institution_id'):
        with op.batch_alter_table('models', schema=None) as batch_op:
            batch_op.add_column(sa.Column('institution_id', sa.Integer(), nullable=True))
    op.create_index('ix_models_institution_id', 'models', ['institution_id'],
                    unique=False, if_not_exists=True)

    if not _column_exists('payments', 'institution_id'):
        with op.batch_alter_table('payments', schema=None) as batch_op:
            batch_op.add_column(sa.Column('institution_id', sa.Integer(), nullable=True))
    op.create_index('ix_payments_institution_id', 'payments', ['institution_id'],
                    unique=False, if_not_exists=True)


def downgrade():
    op.drop_index('ix_payments_institution_id', table_name='payments')
    if _column_exists('payments', 'institution_id'):
        with op.batch_alter_table('payments', schema=None) as batch_op:
            batch_op.drop_column('institution_id')

    op.drop_index('ix_models_institution_id', table_name='models')
    if _column_exists('models', 'institution_id'):
        with op.batch_alter_table('models', schema=None) as batch_op:
            batch_op.drop_column('institution_id')
