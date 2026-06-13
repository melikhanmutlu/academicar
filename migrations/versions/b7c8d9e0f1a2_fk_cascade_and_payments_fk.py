"""Enforce cascade/SET NULL at the DB level for existing Postgres deployments.

Fresh deploys build their schema from ``db.create_all()`` (see CLAUDE.md), which
now picks up the model-declared ``ondelete`` rules, so this incremental migration
only needs to bring an ALREADY-migrated PostgreSQL database in line:

* I8 — child FKs gain ``ON DELETE CASCADE`` so a bulk/raw delete of a User /
  Paper / Model3D cannot leave orphaned papers, models, annotations, QR links,
  model versions, or conversion jobs (and a resolvable QR pointing at a deleted
  model).
* I6 — ``payments.model_id`` gains its missing FK with ``ON DELETE SET NULL`` so
  deleting a model nulls the reference while the payment/invoice audit row
  survives (the column was added without a constraint).

Postgres-only and idempotent (DROP CONSTRAINT IF EXISTS then ADD). A no-op on
SQLite/other engines, where create_all already materialized the constraints.
"""
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

# (table, constraint_name, column, ref_table, ref_col, on_delete)
_FKS = [
    ("papers", "papers_user_id_fkey", "user_id", "users", "id", "CASCADE"),
    ("models", "models_paper_id_fkey", "paper_id", "papers", "id", "CASCADE"),
    ("models", "models_user_id_fkey", "user_id", "users", "id", "CASCADE"),
    ("model_annotations", "model_annotations_model_id_fkey", "model_id", "models", "id", "CASCADE"),
    ("qr_links", "qr_links_model_id_fkey", "model_id", "models", "id", "CASCADE"),
    ("model_versions", "model_versions_model_id_fkey", "model_id", "models", "id", "CASCADE"),
    ("conversion_jobs", "conversion_jobs_model_id_fkey", "model_id", "models", "id", "CASCADE"),
    ("payments", "payments_model_id_fkey", "model_id", "models", "id", "SET NULL"),
]


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # fresh deploys / SQLite already have the right schema via create_all
    for table, name, col, ref_t, ref_c, on_delete in _FKS:
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}')
        op.execute(
            f'ALTER TABLE {table} ADD CONSTRAINT {name} '
            f'FOREIGN KEY ({col}) REFERENCES {ref_t}({ref_c}) ON DELETE {on_delete}'
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Restore plain FKs (no cascade); payments.model_id reverts to no constraint.
    for table, name, col, ref_t, ref_c, on_delete in _FKS:
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}')
        if table != "payments":
            op.execute(
                f'ALTER TABLE {table} ADD CONSTRAINT {name} '
                f'FOREIGN KEY ({col}) REFERENCES {ref_t}({ref_c})'
            )
