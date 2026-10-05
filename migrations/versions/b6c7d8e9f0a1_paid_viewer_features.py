"""Backfill the v39 paid-only viewer features into existing license_plans rows

Revision ID: b6c7d8e9f0a1
Revises: b5c6d7e8f9a0
Create Date: 2026-10-05

seed_license_plans never touches a row that already exists, so plans seeded by
older releases would miss the new capability keys. Paid plans gain them, Free
loses them (its old default was "every feature"). Data-only and idempotent;
later admin edits on /admin/pricing are left alone because this runs once.
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "b6c7d8e9f0a1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None

NEW_FEATURES = ("scenes", "scene_ar", "section_plane", "layer_metrics", "figure_export", "comparison")
PAID_KEYS = ("academic", "extended_archive", "institutional")

license_plans = sa.table(
    "license_plans",
    sa.column("id", sa.Integer),
    sa.column("key", sa.String),
    sa.column("features", sa.JSON),
)


def _rewrite(add: bool, keys: tuple[str, ...]):
    bind = op.get_bind()
    if "license_plans" not in inspect(bind).get_table_names():
        return
    rows = bind.execute(sa.select(license_plans.c.id, license_plans.c.key, license_plans.c.features)).fetchall()
    for row_id, key, features in rows:
        if features is None or key not in keys:
            continue  # NULL rows get the Python defaults on the next seed
        current = json.loads(features) if isinstance(features, str) else list(features)
        updated = set(current) | set(NEW_FEATURES) if add else set(current) - set(NEW_FEATURES)
        if updated != set(current):
            bind.execute(
                license_plans.update().where(license_plans.c.id == row_id).values(features=sorted(updated))
            )


def upgrade():
    _rewrite(True, PAID_KEYS)
    _rewrite(False, ("free",))


def downgrade():
    _rewrite(False, PAID_KEYS + ("free",))
