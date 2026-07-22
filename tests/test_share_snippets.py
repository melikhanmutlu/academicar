"""Tests for the ready-to-paste figure caption + citation share package."""
import uuid
from pathlib import Path
from types import SimpleNamespace


def test_build_share_snippets_includes_metadata_and_url():
    from app import build_share_snippets

    paper = SimpleNamespace(authors="Doe J, Roe A", year=2026, title="A study of valves.", doi="10.1234/abcd")
    model = SimpleNamespace(display_name="Mitral valve", original_filename="valve.glb", paper=paper)
    url = "https://academicar.example/m/abc123"

    snippets = build_share_snippets(model, url)
    assert url in snippets["caption"]
    assert "Mitral valve" in snippets["caption"]
    # Citation weaves authors, year, title, a resolvable DOI, and the viewer URL.
    assert "Doe J, Roe A (2026)." in snippets["citation"]
    assert "A study of valves." in snippets["citation"]
    assert "https://doi.org/10.1234/abcd" in snippets["citation"]
    assert url in snippets["citation"]


def test_build_share_snippets_degrades_without_metadata():
    from app import build_share_snippets

    paper = SimpleNamespace(authors=None, year=None, title=None, doi=None)
    model = SimpleNamespace(display_name=None, original_filename="scan.glb", paper=paper)
    url = "https://academicar.example/m/xyz"

    snippets = build_share_snippets(model, url)
    # Falls back to the filename and still produces a usable citation with the URL.
    assert "scan.glb" in snippets["caption"]
    assert "AcademicAR." in snippets["citation"]
    assert url in snippets["citation"]


def test_qr_print_page_renders_share_package(client):
    from tests.conftest import register
    from models import Model3D, Paper, User, db

    register(client, email="qrshare@example.com")
    with client.application.app_context():
        owner = User.query.filter_by(email="qrshare@example.com").one()
        paper = Paper(title="QR Share Paper", slug="qr-share", user_id=owner.id, authors="Smith K", year=2025, is_public=True)
        db.session.add(paper)
        db.session.commit()
        model_id = str(uuid.uuid4())
        model_dir = Path(client.application.config["CONVERTED_FOLDER"]) / model_id
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model.glb").write_bytes(b"glb")
        model = Model3D(
            id=model_id, paper_id=paper.id, user_id=owner.id,
            display_name="Shared model", original_filename="shared.glb",
            glb_path=str(model_dir / "model.glb"), qr_code_path="qr.png", file_size=3,
        )
        db.session.add(model)
        db.session.commit()

    resp = client.get(f"/qr-print/{model_id}")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Figure caption" in html
    assert "Citation" in html
    assert "Smith K (2025)." in html
    assert "Shared model" in html
