import io
from pathlib import Path
from uuid import uuid4

import pytest

from app import create_app
from models import User, db


@pytest.fixture()
def app():
    base_dir = Path("tests_runtime") / uuid4().hex
    instance_dir = base_dir / "instance"
    upload_dir = base_dir / "uploads"
    converted_dir = base_dir / "converted"
    qr_dir = base_dir / "qr_codes"
    pdf_dir = base_dir / "pdfs"
    instance_dir.mkdir(parents=True, exist_ok=True)
    db_path = (instance_dir / "test.db").resolve()
    flask_app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
            "UPLOAD_FOLDER": str(upload_dir.resolve()),
            "CONVERTED_FOLDER": str(converted_dir.resolve()),
            "QR_FOLDER": str(qr_dir.resolve()),
            "PDF_FOLDER": str(pdf_dir.resolve()),
            "SECRET_KEY": "test-secret",
        }
    )
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
    yield flask_app
    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, email="user@example.com", password="password123", username="Test User"):
    return client.post(
        "/auth/register",
        data={
            "username": username,
            "email": email,
            "password": password,
            "confirm": password,
        },
        follow_redirects=True,
    )


def login(client, email="user@example.com", password="password123", follow_redirects=True):
    return client.post(
        "/auth/login",
        data={"email": email, "password": password},
        follow_redirects=follow_redirects,
    )


def create_user(email="user@example.com", password="password123", username="Test User"):
    user = User(email=email, username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def valid_ascii_stl_bytes():
    return b"""solid tetra
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
facet normal 0 1 0
 outer loop
  vertex 0 0 0
  vertex 0 0 1
  vertex 1 0 0
 endloop
endfacet
facet normal 1 0 0
 outer loop
  vertex 0 0 0
  vertex 0 1 0
  vertex 0 0 1
 endloop
endfacet
facet normal 1 1 1
 outer loop
  vertex 1 0 0
  vertex 0 0 1
  vertex 0 1 0
 endloop
endfacet
endsolid tetra
"""


def pdf_bytes():
    return b"%PDF-1.4\n% test\n"


def upload_file_bytes(content, filename):
    return io.BytesIO(content), filename
