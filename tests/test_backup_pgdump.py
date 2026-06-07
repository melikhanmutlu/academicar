"""Tests for the best-effort pg_dump branch of the admin backup archive."""
import io
import zipfile

import app as app_module


def _new_zip():
    buf = io.BytesIO()
    return zipfile.ZipFile(buf, "w"), buf


def test_dump_skips_non_postgres():
    zf, _ = _new_zip()
    assert app_module._dump_postgres_into_zip(zf, "sqlite:///x.db") is False
    zf.close()


def test_dump_returns_false_without_pg_dump(monkeypatch):
    monkeypatch.setattr(app_module.shutil, "which", lambda name: None)
    zf, _ = _new_zip()
    uri = "postgresql+psycopg://u:p@host:5432/db"
    assert app_module._dump_postgres_into_zip(zf, uri) is False
    zf.close()


def test_dump_writes_sql_and_normalizes_uri(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(app_module.shutil, "which", lambda name: "/usr/bin/pg_dump")

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        # pg_dump writes to the --file path; emulate that.
        out_path = cmd[cmd.index("--file") + 1]
        with open(out_path, "w") as fh:
            fh.write("-- fake dump\n")

        class _R:
            returncode = 0
            stderr = ""

        return _R()

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)

    zf, buf = _new_zip()
    uri = "postgresql+psycopg://u:p@host:5432/db"
    assert app_module._dump_postgres_into_zip(zf, uri) is True
    zf.close()

    # Driver-qualified scheme is normalized to a plain libpq URL for pg_dump.
    assert "postgresql://u:p@host:5432/db" in captured["cmd"]
    assert "postgresql+psycopg://" not in " ".join(captured["cmd"])

    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as result:
        assert "database/postgres_dump.sql" in result.namelist()


def test_dump_returns_false_on_pg_dump_error(monkeypatch):
    monkeypatch.setattr(app_module.shutil, "which", lambda name: "/usr/bin/pg_dump")

    def fake_run(cmd, capture_output, text, timeout):
        class _R:
            returncode = 1
            stderr = "connection refused"

        return _R()

    monkeypatch.setattr(app_module.subprocess, "run", fake_run)
    zf, _ = _new_zip()
    uri = "postgresql+psycopg://u:p@host:5432/db"
    assert app_module._dump_postgres_into_zip(zf, uri) is False
    zf.close()
