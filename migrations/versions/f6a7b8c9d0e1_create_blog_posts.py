"""create blog_posts table

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-06

Admin-authored blog posts (Markdown body). Local SQLite gets the table from
db.create_all(); PostgreSQL needs this migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def _table_exists(table_name):
    bind = op.get_bind()
    insp = inspect(bind)
    return table_name in insp.get_table_names()


def upgrade():
    if not _table_exists('blog_posts'):
        op.create_table(
            'blog_posts',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('slug', sa.String(250), nullable=False),
            sa.Column('title', sa.String(300), nullable=False),
            sa.Column('description', sa.String(500), nullable=True),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('tags', sa.String(300), nullable=True),
            sa.Column('persona', sa.String(120), nullable=True),
            sa.Column('author', sa.String(120), nullable=True),
            sa.Column('read_minutes', sa.Integer(), nullable=True),
            sa.Column('is_published', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('slug'),
        )
        op.create_index('ix_blog_posts_slug', 'blog_posts', ['slug'], unique=True)
        op.create_index('ix_blog_posts_is_published', 'blog_posts', ['is_published'])
        op.create_index('ix_blog_posts_created_at', 'blog_posts', ['created_at'])


def downgrade():
    if _table_exists('blog_posts'):
        op.drop_index('ix_blog_posts_created_at', table_name='blog_posts')
        op.drop_index('ix_blog_posts_is_published', table_name='blog_posts')
        op.drop_index('ix_blog_posts_slug', table_name='blog_posts')
        op.drop_table('blog_posts')
