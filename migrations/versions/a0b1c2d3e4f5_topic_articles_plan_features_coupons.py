"""topic articles, presentation attachments, plan features, and coupons

Revision ID: a0b1c2d3e4f5
Revises: b1c2d3e4f5a6
Create Date: 2026-07-19
"""

from contextlib import suppress

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "a0b1c2d3e4f5"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    return column_name in {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def upgrade():  # noqa: C901
    if not _column_exists("papers", "department"):
        with op.batch_alter_table("papers") as batch:
            batch.add_column(sa.Column("department", sa.String(length=120), nullable=True))
            batch.create_index("ix_papers_department", ["department"], unique=False)

    if not inspect(op.get_bind()).has_table("project_articles"):
        op.create_table(
            "project_articles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=True),
            sa.Column("authors", sa.String(length=500), nullable=True),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("doi", sa.String(length=200), nullable=True),
            sa.Column("pmid", sa.String(length=100), nullable=True),
            sa.Column("abstract", sa.Text(), nullable=True),
            sa.Column("pdf_path", sa.String(length=500), nullable=True),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["papers.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_project_articles_project_id", "project_articles", ["project_id"], unique=False)
        # Preserve existing scholarly metadata as the first related article.
        op.execute(sa.text("""
            INSERT INTO project_articles
                (project_id, title, authors, year, doi, pmid, abstract, pdf_path, order_index, created_at, updated_at)
            SELECT id, title, authors, year, doi, pmid, abstract, pdf_path, 0, created_at, created_at
            FROM papers
            WHERE authors IS NOT NULL OR doi IS NOT NULL OR pmid IS NOT NULL OR pdf_path IS NOT NULL
        """))

    if not inspect(op.get_bind()).has_table("project_attachments"):
        op.create_table(
            "project_attachments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=True),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("file_type", sa.String(length=12), nullable=False),
            sa.Column("source_path", sa.String(length=500), nullable=False),
            sa.Column("preview_pdf_path", sa.String(length=500), nullable=True),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["project_id"], ["papers.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_project_attachments_project_id", "project_attachments", ["project_id"], unique=False)

    with op.batch_alter_table("license_plans") as batch:
        if not _column_exists("license_plans", "max_models_per_project"):
            batch.add_column(sa.Column("max_models_per_project", sa.Integer(), nullable=True))
        if not _column_exists("license_plans", "features"):
            batch.add_column(sa.Column("features", sa.JSON(), nullable=True))

    if not inspect(op.get_bind()).has_table("coupons"):
        op.create_table(
            "coupons",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(length=64), nullable=False),
            sa.Column("percent_off", sa.Integer(), nullable=True),
            sa.Column("fixed_discount_usd_cents", sa.Integer(), nullable=True),
            sa.Column("applicable_plan_keys", sa.JSON(), nullable=True),
            sa.Column("max_redemptions", sa.Integer(), nullable=True),
            sa.Column("redemption_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("starts_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("code"),
        )
        op.create_index("ix_coupons_code", "coupons", ["code"], unique=True)
        op.create_index("ix_coupons_is_active", "coupons", ["is_active"], unique=False)

    with op.batch_alter_table("payments") as batch:
        if not _column_exists("payments", "amount_before_discount"):
            batch.add_column(sa.Column("amount_before_discount", sa.Integer(), nullable=True))
        if not _column_exists("payments", "discount_amount"):
            batch.add_column(sa.Column("discount_amount", sa.Integer(), nullable=False, server_default="0"))
        if not _column_exists("payments", "coupon_id"):
            batch.add_column(sa.Column("coupon_id", sa.Integer(), nullable=True))
            batch.create_foreign_key("fk_payments_coupon_id", "coupons", ["coupon_id"], ["id"], ondelete="SET NULL")
            batch.create_index("ix_payments_coupon_id", ["coupon_id"], unique=False)
        if not _column_exists("payments", "coupon_code"):
            batch.add_column(sa.Column("coupon_code", sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table("payments") as batch:
        for index_name in ("ix_payments_coupon_id",):
            with suppress(Exception):
                batch.drop_index(index_name)
        for column in ("coupon_code", "coupon_id", "discount_amount", "amount_before_discount"):
            if _column_exists("payments", column):
                batch.drop_column(column)
    if inspect(op.get_bind()).has_table("coupons"):
        op.drop_table("coupons")
    with op.batch_alter_table("license_plans") as batch:
        if _column_exists("license_plans", "features"):
            batch.drop_column("features")
        if _column_exists("license_plans", "max_models_per_project"):
            batch.drop_column("max_models_per_project")
    if inspect(op.get_bind()).has_table("project_attachments"):
        op.drop_table("project_attachments")
    if inspect(op.get_bind()).has_table("project_articles"):
        op.drop_table("project_articles")
    if _column_exists("papers", "department"):
        with op.batch_alter_table("papers") as batch:
            batch.drop_index("ix_papers_department")
            batch.drop_column("department")
