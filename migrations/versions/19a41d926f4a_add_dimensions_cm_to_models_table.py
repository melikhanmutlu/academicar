"""add dimensions_cm to models table and create missing indexes

Revision ID: 19a41d926f4a
Revises: 042d3cce9a33
Create Date: 2026-06-02 00:39:18.412394

dimensions_cm was added to the Model3D SQLAlchemy model but was never
included in any Alembic migration. Local SQLite picked it up via
db.create_all(); the Railway PostgreSQL database did not, causing a
ProgrammingError on every query that eager-loads Model3D rows.

Also creates ix_models_license_status, ix_models_license_type and
ix_models_public_id which were similarly absent from the production schema.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = '19a41d926f4a'
down_revision = '042d3cce9a33'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    """Return True if the column already exists (handles SQLite local dev)."""
    bind = op.get_bind()
    insp = inspect(bind)
    columns = [col['name'] for col in insp.get_columns(table_name)]
    return column_name in columns


def upgrade():
    # Add dimensions_cm only when it is missing (local SQLite already has it
    # from db.create_all; PostgreSQL does not).
    if not _column_exists('models', 'dimensions_cm'):
        with op.batch_alter_table('models', schema=None) as batch_op:
            batch_op.add_column(sa.Column('dimensions_cm', sa.String(50), nullable=True))

    # Create indexes with if_not_exists so the migration is idempotent.
    op.create_index('ix_models_license_status', 'models', ['license_status'],
                    unique=False, if_not_exists=True)
    op.create_index('ix_models_license_type', 'models', ['license_type'],
                    unique=False, if_not_exists=True)
    op.create_index('ix_models_public_id', 'models', ['public_id'],
                    unique=True, if_not_exists=True)


def downgrade():
    op.drop_index('ix_models_public_id', table_name='models')
    op.drop_index('ix_models_license_type', table_name='models')
    op.drop_index('ix_models_license_status', table_name='models')
    if _column_exists('models', 'dimensions_cm'):
        with op.batch_alter_table('models', schema=None) as batch_op:
            batch_op.drop_column('dimensions_cm')
