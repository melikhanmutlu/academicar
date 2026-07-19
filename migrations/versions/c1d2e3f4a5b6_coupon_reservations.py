"""coupon checkout reservations

Revision ID: c1d2e3f4a5b6
Revises: a0b1c2d3e4f5
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "c1d2e3f4a5b6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    return column_name in {
        column["name"] for column in inspect(op.get_bind()).get_columns(table_name)
    }


def upgrade():
    with op.batch_alter_table("coupons") as batch:
        if not _column_exists("coupons", "reservation_count"):
            batch.add_column(
                sa.Column(
                    "reservation_count",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )
    with op.batch_alter_table("payments") as batch:
        if not _column_exists("payments", "coupon_reservation_active"):
            batch.add_column(
                sa.Column(
                    "coupon_reservation_active",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade():
    with op.batch_alter_table("payments") as batch:
        if _column_exists("payments", "coupon_reservation_active"):
            batch.drop_column("coupon_reservation_active")
    with op.batch_alter_table("coupons") as batch:
        if _column_exists("coupons", "reservation_count"):
            batch.drop_column("reservation_count")
