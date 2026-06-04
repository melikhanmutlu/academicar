"""Add FK indexes, ModelVersion unique constraint, make glb_path nullable

Brings databases created before these model changes in line with the current
schema. On a fresh deploy the schema is bootstrapped by ``db.create_all()`` and
stamped to head, so this migration only ever runs against pre-existing
PostgreSQL databases. Every step is guarded so re-running it (or running it on a
DB that already has some of the objects) is a safe no-op.

Revision ID: b7f2c1a9d4e3
Revises: 19a41d926f4a
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b7f2c1a9d4e3"
down_revision = "19a41d926f4a"
branch_labels = None
depends_on = None


def _index_names(bind, table):
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def _unique_names(bind, table):
    return {uc["name"] for uc in sa.inspect(bind).get_unique_constraints(table)}


def _column_nullable(bind, table, column):
    for col in sa.inspect(bind).get_columns(table):
        if col["name"] == column:
            return col["nullable"]
    return None


def upgrade():
    bind = op.get_bind()

    # 1) Indexes on foreign-key columns (JOIN / CASCADE DELETE performance).
    if "ix_papers_user_id" not in _index_names(bind, "papers"):
        op.create_index("ix_papers_user_id", "papers", ["user_id"])
    models_idx = _index_names(bind, "models")
    if "ix_models_paper_id" not in models_idx:
        op.create_index("ix_models_paper_id", "models", ["paper_id"])
    if "ix_models_user_id" not in models_idx:
        op.create_index("ix_models_user_id", "models", ["user_id"])

    # 2) models.glb_path -> nullable (a row exists before its GLB is produced).
    if _column_nullable(bind, "models", "glb_path") is False:
        with op.batch_alter_table("models") as batch:
            batch.alter_column(
                "glb_path", existing_type=sa.String(length=500), nullable=True
            )

    # 3) Unique (model_id, version_number) so concurrent replacements can't
    #    insert duplicate versions.
    if "uq_model_version_number" not in _unique_names(bind, "model_versions"):
        with op.batch_alter_table("model_versions") as batch:
            batch.create_unique_constraint(
                "uq_model_version_number", ["model_id", "version_number"]
            )


def downgrade():
    bind = op.get_bind()

    if "uq_model_version_number" in _unique_names(bind, "model_versions"):
        with op.batch_alter_table("model_versions") as batch:
            batch.drop_constraint("uq_model_version_number", type_="unique")

    if _column_nullable(bind, "models", "glb_path") is True:
        with op.batch_alter_table("models") as batch:
            batch.alter_column(
                "glb_path", existing_type=sa.String(length=500), nullable=False
            )

    models_idx = _index_names(bind, "models")
    if "ix_models_user_id" in models_idx:
        op.drop_index("ix_models_user_id", table_name="models")
    if "ix_models_paper_id" in models_idx:
        op.drop_index("ix_models_paper_id", table_name="models")
    if "ix_papers_user_id" in _index_names(bind, "papers"):
        op.drop_index("ix_papers_user_id", table_name="papers")
