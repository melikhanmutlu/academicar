"""add composite/foreign-key indexes used by the admin panel

Revision ID: a1d2e3f4b5c6
Revises: e8f9a0b1c2d3
Create Date: 2026-10-06 09:00:00.000000

The admin audit log and analytics pages filter audit_logs by
(event_type, timestamp) and user_id, and analytics_events by
(event_name, occurred_at); without these indexes each page scans the whole
table as it grows. The same indexes are declared on the SQLAlchemy models so
fresh deployments materialised via db.create_all() have them; this migration
backfills existing databases.

Idempotent: every create_index uses if_not_exists, so running it on a database
that already has the index (from create_all) is a no-op.
"""
from alembic import op


revision = 'a1d2e3f4b5c6'
down_revision = 'e8f9a0b1c2d3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_audit_logs_event_type_timestamp', 'audit_logs',
                    ['event_type', 'timestamp'], unique=False, if_not_exists=True)
    op.create_index('ix_audit_logs_user_id', 'audit_logs', ['user_id'],
                    unique=False, if_not_exists=True)
    op.create_index('ix_analytics_events_event_name_occurred_at', 'analytics_events',
                    ['event_name', 'occurred_at'], unique=False, if_not_exists=True)


def downgrade():
    op.drop_index('ix_analytics_events_event_name_occurred_at', table_name='analytics_events', if_exists=True)
    op.drop_index('ix_audit_logs_user_id', table_name='audit_logs', if_exists=True)
    op.drop_index('ix_audit_logs_event_type_timestamp', table_name='audit_logs', if_exists=True)
