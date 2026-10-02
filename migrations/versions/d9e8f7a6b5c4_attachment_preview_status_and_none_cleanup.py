"""attachment preview status + clean literal "None" article fields

Revision ID: d9e8f7a6b5c4
Revises: c1d2e3f4a5b6
Create Date: 2026-10-02

* project_attachments.preview_status: PowerPoint previews are now converted by
  the worker instead of inside the web request, so each attachment records
  where its preview stands (pending / processing / ready / failed /
  unavailable).
* project_articles: the project edit form used to render empty optional
  fields as the text "None", which was saved back on the next edit and
  produced links such as https://doi.org/None. Turn those strings back into
  NULL. A real title/DOI/PMID/author list is never the bare word "None".

Idempotent: the column is added only if missing and the UPDATEs are no-ops
once the data is clean.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "d9e8f7a6b5c4"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None

_ARTICLE_TEXT_COLUMNS = ("title", "authors", "doi", "pmid", "abstract")


def _table_exists(table_name):
    return table_name in inspect(op.get_bind()).get_table_names()


def _column_exists(table_name, column_name):
    insp = inspect(op.get_bind())
    return column_name in [col["name"] for col in insp.get_columns(table_name)]


def upgrade():
    if _table_exists("project_attachments") and not _column_exists("project_attachments", "preview_status"):
        with op.batch_alter_table("project_attachments", schema=None) as batch_op:
            batch_op.add_column(sa.Column("preview_status", sa.String(length=20), nullable=True))
            batch_op.create_index("ix_project_attachments_preview_status", ["preview_status"], unique=False)
    if _table_exists("project_articles"):
        existing = {col["name"] for col in inspect(op.get_bind()).get_columns("project_articles")}
        for column in _ARTICLE_TEXT_COLUMNS:
            if column in existing:
                op.execute(f"UPDATE project_articles SET {column} = NULL WHERE {column} = 'None'")


def downgrade():
    if _table_exists("project_attachments") and _column_exists("project_attachments", "preview_status"):
        with op.batch_alter_table("project_attachments", schema=None) as batch_op:
            batch_op.drop_index("ix_project_attachments_preview_status")
            batch_op.drop_column("preview_status")
    # The "None" cleanup is not reversible (and should not be).
