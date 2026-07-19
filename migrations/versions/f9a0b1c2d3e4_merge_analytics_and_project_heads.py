"""Merge the analytics and project-workflow migration heads.

Two migrations were branched off ``f8a9b0c1d2e3`` independently, leaving the
graph with two heads:

* ``58e532f1a9b0`` (create_analytics_events)
* ``c9d0e1f2a3b4`` (add_project_workflow_fields)

With two heads, ``flask db upgrade`` aborts with "Multiple head revisions are
present", which on Railway short-circuits the ``&& python worker.py`` chain and
stops the conversion worker from booting — uploads then hang in "Processing
model". This is an empty merge revision (no schema change) that rejoins both
branches into a single head so upgrades have an unambiguous target.
"""

# revision identifiers, used by Alembic.
revision = "f9a0b1c2d3e4"
down_revision = ("58e532f1a9b0", "c9d0e1f2a3b4")
branch_labels = None
depends_on = None


def upgrade():
    # Pure merge point — both parents already applied their own schema changes.
    pass


def downgrade():
    pass
