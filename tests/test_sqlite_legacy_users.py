"""Booting on a SQLite database created before users.email_verified_at."""
import sqlite3
from pathlib import Path
from uuid import uuid4

from app import create_app
from models import User, db


def test_boot_adds_email_verified_at_before_syncing_admins(caplog):
    base = Path("tests_runtime") / uuid4().hex
    base.mkdir(parents=True)
    db_path = (base / "legacy.db").resolve()
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR(120) NOT NULL UNIQUE, username VARCHAR(80) NOT NULL, "
        "password_hash VARCHAR(256), google_id VARCHAR(100), avatar_url VARCHAR(500), is_admin BOOLEAN NOT NULL DEFAULT 0, "
        "deactivated_at DATETIME, created_at DATETIME)"
    )
    con.execute("INSERT INTO users (email, username, is_admin, created_at) VALUES ('boss@example.com', 'Boss', 0, '2026-01-01')")
    con.commit()
    con.close()

    caplog.set_level("ERROR")
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "SECRET_KEY": "test-secret",
        "ADMIN_EMAILS": ["boss@example.com"],
    })
    assert "Could not sync configured admin users" not in caplog.text
    with app.app_context():
        user = User.query.filter_by(email="boss@example.com").one()
        assert user.is_admin
        assert user.email_verified_at is not None  # existing accounts count as verified
        db.session.remove()
