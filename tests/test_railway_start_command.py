"""The Railway start command must finish migrations before anything serves.

It used to read ``flask db upgrade && python worker.py & …; gunicorn …``:
``&`` binds the whole ``upgrade && worker`` list into the background, so
gunicorn started before the schema was migrated, and a failed migration left
the site up with no worker at all.
"""
import json
import os
import shutil
import stat
import subprocess

import pytest

RAILWAY_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "railway.json")


def _start_command():
    with open(RAILWAY_JSON) as fh:
        return json.load(fh)["deploy"]["startCommand"]


def _stub(directory, name, body):
    path = directory / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, migrate_rc):
    log = tmp_path / "order.log"
    # A slow migration: anything that does not wait for it logs first.
    _stub(tmp_path, "flask", f'sleep 0.5; echo migrate >> "{log}"; exit {migrate_rc}')
    _stub(tmp_path, "python", f'echo worker >> "{log}"')
    _stub(tmp_path, "gunicorn", f'echo web >> "{log}"; sleep 0.3')
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "PORT": "1"}
    result = subprocess.run(["sh", "-c", _start_command()], env=env, timeout=20)
    lines = log.read_text().split() if log.exists() else []
    return result.returncode, lines


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_railway_start_command_migrates_before_serving(tmp_path):
    rc, lines = _run(tmp_path, migrate_rc=0)
    assert rc == 0
    assert lines[0] == "migrate"
    assert set(lines) == {"migrate", "worker", "web"}


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_railway_start_command_stops_when_migration_fails(tmp_path):
    rc, lines = _run(tmp_path, migrate_rc=1)
    assert rc != 0
    assert lines == ["migrate"]  # neither the worker nor the web process start
