"""Guard against a forked Alembic migration graph.

Regression test for the "Multiple head revisions are present" failure: when a
new migration is branched off an old revision instead of the current head, the
graph grows two heads and ``flask db upgrade`` aborts. On Railway a failed
upgrade stops the container start (see ``test_railway_start_command_*``);
before that, it silently prevented the worker from booting while the web
process kept serving, and every freshly uploaded model stayed wedged in
"Processing model" forever.

A single head keeps ``flask db upgrade`` (and app.py's ``get_current_head()``
stamp path) unambiguous.
"""
import os

from alembic.script import ScriptDirectory

MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations")


def test_migrations_have_a_single_head():
    script = ScriptDirectory(MIGRATIONS_DIR)
    heads = script.get_heads()
    assert len(heads) == 1, (
        "Alembic migration graph has multiple heads: "
        f"{sorted(heads)}. Add a merge migration ('flask db merge heads') so "
        "'flask db upgrade' has an unambiguous target."
    )
