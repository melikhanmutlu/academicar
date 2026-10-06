"""Add the slice view and custom threshold features; open scenes, scene AR and
the section plane to every plan

Revision ID: c7d8e9f0a1b2
Revises: e8f9a0b1c2d3
Create Date: 2026-10-06

seed_license_plans never touches a row that already exists, so existing
license_plans rows need the new keys added here. Every plan gains them (for
now; admins can remove them again on /admin/pricing). Data-only and idempotent.
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "c7d8e9f0a1b2"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None

NEW_FEATURES = ("slice_view", "custom_threshold")
OPENED_TO_FREE = ("scenes", "scene_ar", "section_plane")
ALL_KEYS = ("free", "academic", "extended_archive", "institutional")

license_plans = sa.table(
    "license_plans",
    sa.column("id", sa.Integer),
    sa.column("key", sa.String),
    sa.column("features", sa.JSON),
)


def _rewrite(add: bool, keys: tuple[str, ...], features: tuple[str, ...]):
    bind = op.get_bind()
    if "license_plans" not in inspect(bind).get_table_names():
        return
    rows = bind.execute(sa.select(license_plans.c.id, license_plans.c.key, license_plans.c.features)).fetchall()
    for row_id, key, current in rows:
        if current is None or key not in keys:
            continue  # NULL rows get the Python defaults on the next seed
        current = json.loads(current) if isinstance(current, str) else list(current)
        updated = set(current) | set(features) if add else set(current) - set(features)
        if updated != set(current):
            bind.execute(
                license_plans.update().where(license_plans.c.id == row_id).values(features=sorted(updated))
            )


def upgrade():
    _rewrite(True, ALL_KEYS, NEW_FEATURES)
    _rewrite(True, ("free",), OPENED_TO_FREE)


def downgrade():
    _rewrite(False, ALL_KEYS, NEW_FEATURES)
    _rewrite(False, ("free",), OPENED_TO_FREE)
