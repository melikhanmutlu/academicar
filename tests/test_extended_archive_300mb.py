"""Extended Archive accepts files up to 300 MB (Academic stays at 200 MB)."""
import importlib.util
import os

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from converters import base_converter
from licensing import default_license_plans

MB = 1024 * 1024


def _migration():
    name = "a3b4c5d6e7f8_extended_archive_300mb.py"
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "versions", name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_defaults_and_converter_cap():
    plans = default_license_plans()
    assert plans["extended_archive"].storage_limit_bytes == 300 * MB
    assert plans["academic"].storage_limit_bytes == 200 * MB
    if not (os.environ.get("MAX_MODEL_FILE_BYTES") or os.environ.get("MAX_CONTENT_LENGTH")):
        assert base_converter.MAX_MODEL_FILE_BYTES >= 300 * MB


def _run(rows, direction):
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    table = sa.Table(
        "license_plans", meta,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String),
        sa.Column("storage_limit_bytes", sa.Integer),
    )
    meta.create_all(engine)
    migration = _migration()
    with engine.begin() as conn:
        conn.execute(table.insert(), rows)
        migration.op = Operations(MigrationContext.configure(conn))
        getattr(migration, direction)()
        return dict(conn.execute(sa.select(table.c.key, table.c.storage_limit_bytes)).fetchall())


def test_migration_raises_only_the_default_extended_archive_row():
    result = _run(
        [{"key": "academic", "storage_limit_bytes": 200 * MB},
         {"key": "extended_archive", "storage_limit_bytes": 200 * MB}],
        "upgrade",
    )
    assert result == {"academic": 200 * MB, "extended_archive": 300 * MB}


def test_migration_keeps_an_admin_custom_value():
    result = _run([{"key": "extended_archive", "storage_limit_bytes": 250 * MB}], "upgrade")
    assert result == {"extended_archive": 250 * MB}


def test_migration_downgrade():
    result = _run([{"key": "extended_archive", "storage_limit_bytes": 300 * MB}], "downgrade")
    assert result == {"extended_archive": 200 * MB}
