"""Add project workflow and unlisted-sharing fields.

The table retains its historical ``papers`` name so existing production data,
foreign keys, audit rows and QR links are migrated without a destructive table
or column rename. In the application this record is now presented as Project.
"""
from alembic import op
import sqlalchemy as sa


revision = "c9d0e1f2a3b4"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("papers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("project_type", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("workflow_stage", sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column("visibility", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("share_token", sa.String(length=64), nullable=True))

    # Preserve each existing row's public/private behaviour exactly.
    op.execute("UPDATE papers SET project_type = 'publication' WHERE project_type IS NULL")
    op.execute("UPDATE papers SET workflow_stage = 'published' WHERE workflow_stage IS NULL")
    op.execute("UPDATE papers SET visibility = CASE WHEN is_public THEN 'public' ELSE 'private' END WHERE visibility IS NULL")

    with op.batch_alter_table("papers", schema=None) as batch_op:
        batch_op.alter_column("project_type", nullable=False, server_default="research_project")
        batch_op.alter_column("workflow_stage", nullable=False, server_default="in_progress")
        batch_op.alter_column("visibility", nullable=False, server_default="private")
        batch_op.create_index("ix_papers_project_type", ["project_type"], unique=False)
        batch_op.create_index("ix_papers_workflow_stage", ["workflow_stage"], unique=False)
        batch_op.create_index("ix_papers_visibility", ["visibility"], unique=False)
        batch_op.create_index("ix_papers_share_token", ["share_token"], unique=True)


def downgrade():
    with op.batch_alter_table("papers", schema=None) as batch_op:
        batch_op.drop_index("ix_papers_share_token")
        batch_op.drop_index("ix_papers_visibility")
        batch_op.drop_index("ix_papers_workflow_stage")
        batch_op.drop_index("ix_papers_project_type")
        batch_op.drop_column("share_token")
        batch_op.drop_column("visibility")
        batch_op.drop_column("workflow_stage")
        batch_op.drop_column("project_type")
