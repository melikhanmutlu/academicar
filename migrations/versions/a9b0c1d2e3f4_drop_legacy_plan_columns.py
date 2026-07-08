"""drop legacy user-plan and paper-package columns

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-08

Licensing is model-based only (Model3D.license_type + access windows).
The account-level users.plan and the paper-level packaging/payment/expiry
columns (package_type, payment_status, payment_provider, payment_reference,
expires_at) were dead: nothing read them for behavior. This migration
removes them; Procfile/railway run `flask db upgrade` before the app
starts, so no code writes these columns by the time it boots.
"""
from alembic import op
from sqlalchemy import inspect


revision = 'a9b0c1d2e3f4'
down_revision = 'f8a9b0c1d2e3'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    insp = inspect(op.get_bind())
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    if _column_exists('users', 'plan'):
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.drop_column('plan')
    for column in ('package_type', 'payment_status', 'payment_provider', 'payment_reference', 'expires_at'):
        if _column_exists('papers', column):
            with op.batch_alter_table('papers', schema=None) as batch_op:
                batch_op.drop_column(column)


def downgrade():
    import sqlalchemy as sa

    if not _column_exists('users', 'plan'):
        with op.batch_alter_table('users', schema=None) as batch_op:
            batch_op.add_column(sa.Column('plan', sa.String(30), nullable=False, server_default='free'))
    additions = {
        'package_type': sa.Column('package_type', sa.String(30), nullable=False, server_default='model_based'),
        'payment_status': sa.Column('payment_status', sa.String(30), nullable=False, server_default='model_based'),
        'payment_provider': sa.Column('payment_provider', sa.String(50), nullable=True),
        'payment_reference': sa.Column('payment_reference', sa.String(200), nullable=True),
        'expires_at': sa.Column('expires_at', sa.DateTime(), nullable=True),
    }
    for name, column in additions.items():
        if not _column_exists('papers', name):
            with op.batch_alter_table('papers', schema=None) as batch_op:
                batch_op.add_column(column)
