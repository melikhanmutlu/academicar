"""Raise the Extended Archive per-model file limit from 200 MB to 300 MB

Revision ID: a3b4c5d6e7f8
Revises: e9f0a1b2c3d4
Create Date: 2026-10-20

seed_license_plans never touches a row that already exists, so the live
license_plans row is updated here. Only a row still on the old 200 MB default
changes; a value an admin set on /admin/pricing is left alone. Data-only.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "a3b4c5d6e7f8"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None

MB = 1024 * 1024
OLD_LIMIT = 200 * MB
NEW_LIMIT = 300 * MB

license_plans = sa.table(
    "license_plans",
    sa.column("key", sa.String),
    sa.column("storage_limit_bytes", sa.Integer),
)


def _set_limit(current: int, new: int):
    bind = op.get_bind()
    if "license_plans" not in inspect(bind).get_table_names():
        return
    bind.execute(
        license_plans.update()
        .where(license_plans.c.key == "extended_archive")
        .where(license_plans.c.storage_limit_bytes == current)
        .values(storage_limit_bytes=new)
    )


def upgrade():
    _set_limit(OLD_LIMIT, NEW_LIMIT)


def downgrade():
    _set_limit(NEW_LIMIT, OLD_LIMIT)
