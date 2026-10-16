"""models.embed_config and the embed plan features

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-10-15

embed_config keeps the owner's saved embed settings. seed_license_plans never
touches a row that already exists, so the new feature keys are added here:
"embed" to every plan, the full layout and branding options to the paid plans
(admins can change both on /admin/pricing). Data changes are idempotent.
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None

ALL_KEYS = ("free", "academic", "extended_archive", "institutional")
PAID_KEYS = ("academic", "extended_archive", "institutional")
EMBED = ("embed",)
EMBED_PAID = ("embed_full_layout", "embed_white_label")

license_plans = sa.table(
    "license_plans",
    sa.column("id", sa.Integer),
    sa.column("key", sa.String),
    sa.column("features", sa.JSON),
)


def _rewrite(add, keys, features):
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
            bind.execute(license_plans.update().where(license_plans.c.id == row_id).values(features=sorted(updated)))


def upgrade():
    insp = inspect(op.get_bind())
    if "models" in insp.get_table_names() and "embed_config" not in {c["name"] for c in insp.get_columns("models")}:
        with op.batch_alter_table("models", schema=None) as batch_op:
            batch_op.add_column(sa.Column("embed_config", sa.JSON(), nullable=True))
    _rewrite(True, ALL_KEYS, EMBED)
    _rewrite(True, PAID_KEYS, EMBED_PAID)


def downgrade():
    _rewrite(False, ALL_KEYS, EMBED + EMBED_PAID)
    insp = inspect(op.get_bind())
    if "models" in insp.get_table_names() and "embed_config" in {c["name"] for c in insp.get_columns("models")}:
        with op.batch_alter_table("models", schema=None) as batch_op:
            batch_op.drop_column("embed_config")
