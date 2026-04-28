from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

from models import Model3D, Paper, db


def test_register_login_paper_create_and_delete(client):
    from tests.conftest import login, register

    response = register(client)
    assert response.status_code == 200

    response = login(client)
    assert response.status_code == 200

    response = client.post(
        "/papers/new",
        data={"title": "Pilot Paper", "year": "2026", "field": "Engineering"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        paper = Paper.query.filter_by(title="Pilot Paper").one()
        slug = paper.slug

    response = client.post(f"/papers/{slug}/delete", follow_redirects=True)
    assert response.status_code == 200
    with client.application.app_context():
        assert Paper.query.count() == 0


def test_authenticated_user_can_view_landing_page(client):
    from tests.conftest import login, register

    register(client)
    login(client)

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "AcademicAR" in response.get_data(as_text=True)


def test_paper_delete_cleans_files_after_commit(client):
    from tests.conftest import create_user

    with client.application.app_context():
        user = create_user()
        paper = Paper(title="Cleanup Paper", slug="cleanup-paper", user_id=user.id, pdf_path="paper.pdf")
        db.session.add(paper)
        db.session.commit()
        model_id = "22222222-2222-4222-8222-222222222222"
        model_dir = Path(client.application.config["CONVERTED_FOLDER"]) / model_id
        model_dir.mkdir(parents=True)
        glb_path = model_dir / "model.glb"
        glb_path.write_bytes(b"glTF")
        qr_path = Path(client.application.config["QR_FOLDER"]) / f"qr_{model_id}.png"
        qr_path.write_bytes(b"qr")
        pdf_path = Path(client.application.config["PDF_FOLDER"]) / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")
        model = Model3D(
            id=model_id,
            paper_id=paper.id,
            user_id=user.id,
            original_filename="model.stl",
            glb_path=str(glb_path),
            qr_code_path=qr_path.name,
            file_size=4,
        )
        db.session.add(model)
        db.session.commit()

    from tests.conftest import login

    login(client)
    response = client.post("/papers/cleanup-paper/delete", follow_redirects=True)
    assert response.status_code == 200
    assert not glb_path.exists()
    assert not qr_path.exists()
    assert not pdf_path.exists()


def test_paper_delete_commit_failure_keeps_files(client, monkeypatch):
    from tests.conftest import create_user, login

    with client.application.app_context():
        user = create_user()
        paper = Paper(title="Fail Paper", slug="fail-paper", user_id=user.id, pdf_path="paper.pdf")
        db.session.add(paper)
        db.session.commit()
        pdf_path = Path(client.application.config["PDF_FOLDER"]) / "paper.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n")

    real_commit = db.session.commit

    def fail_once():
        db.session.commit = real_commit
        raise SQLAlchemyError("forced commit failure")

    login(client)
    monkeypatch.setattr(db.session, "commit", fail_once)
    response = client.post("/papers/fail-paper/delete", follow_redirects=True)
    assert response.status_code == 200
    assert pdf_path.exists()


def test_invalid_stl_upload_leaves_no_model(client):
    from tests.conftest import register, upload_file_bytes

    register(client)
    client.post("/papers/new", data={"title": "Upload Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Upload Paper").one().slug

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(b"bad", "bad.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        assert Model3D.query.count() == 0


def test_owner_only_pdf_access(client):
    from tests.conftest import create_user, login, pdf_bytes, upload_file_bytes

    with client.application.app_context():
        create_user()
        create_user(email="other@example.com", username="Other")

    login(client)
    client.post(
        "/papers/new",
        data={"title": "PDF Paper", "pdf": upload_file_bytes(pdf_bytes(), "paper.pdf")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with client.application.app_context():
        paper_id = Paper.query.filter_by(title="PDF Paper").one().id

    assert client.get(f"/pdfs/{paper_id}").status_code == 200

    client.post("/auth/logout")
    login(client, email="other@example.com")
    assert client.get(f"/pdfs/{paper_id}").status_code == 403


def test_public_viewer_and_glb_route(client):
    from tests.conftest import create_user
    from pathlib import Path

    with client.application.app_context():
        user = create_user()
        paper = Paper(title="Public Paper", slug="public-paper", user_id=user.id)
        db.session.add(paper)
        db.session.commit()

        model_id = "11111111-1111-4111-8111-111111111111"
        model_dir = Path(client.application.config["CONVERTED_FOLDER"]) / model_id
        model_dir.mkdir(parents=True)
        glb_path = model_dir / "model.glb"
        glb_path.write_bytes(b"glTF")
        model = Model3D(
            id=model_id,
            paper_id=paper.id,
            user_id=user.id,
            original_filename="model.stl",
            glb_path=str(glb_path),
            qr_code_path="qr.png",
            file_size=4,
        )
        db.session.add(model)
        db.session.commit()

    assert client.get(f"/view/{model_id}").status_code == 200
    glb_response = client.get(f"/files/{model_id}/model.glb")
    assert glb_response.status_code == 200
    assert glb_response.content_type.startswith("model/gltf-binary")
    assert client.get(f"/files/{model_id}/../secret.txt").status_code == 404


def test_valid_stl_upload_creates_model_and_qr(client, monkeypatch):
    import app as app_module
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    class FakeConverter:
        errors = []

        def validate(self, path):
            return True

        def convert(self, input_path, output_path, color=None):
            with open(output_path, "wb") as f:
                f.write(b"glTF")
            return True

    monkeypatch.setattr(app_module, "STLConverter", FakeConverter)

    register(client)
    client.post("/papers/new", data={"title": "Model Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Model Paper").one().slug

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={
            "display_name": "Test Model",
            "description": "Pilot fixture",
            "file": upload_file_bytes(valid_ascii_stl_bytes(), "model.stl"),
            "compliance_confirm": "yes",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = Model3D.query.one()
        assert model.display_name == "Test Model"
        assert model.qr_code_path


def test_real_stl_upload_creates_glb_qr_and_public_viewer(client):
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    register(client)
    client.post("/papers/new", data={"title": "Real STL Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Real STL Paper").one().slug

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "real.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = Model3D.query.one()
        glb_path = Path(model.glb_path)
        qr_path = Path(client.application.config["QR_FOLDER"]) / model.qr_code_path
        model_id = model.id

    assert glb_path.exists()
    assert qr_path.exists()
    assert client.get(f"/view/{model_id}").status_code == 200
    assert client.get(f"/files/{model_id}/model.glb").status_code == 200


def test_glb_upload_creates_model_without_conversion(client):
    from tests.conftest import register, upload_file_bytes

    register(client)
    client.post("/papers/new", data={"title": "Direct GLB Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Direct GLB Paper").one().slug

    glb = b"glTF" + b"\x00" * 24
    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(glb, "direct.glb"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = Model3D.query.one()
        assert model.source_format == "glb"
        assert model.anonymization_confirmed
        assert Path(model.glb_path).read_bytes() == glb


def test_upload_requires_anonymization_confirmation(client):
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    register(client)
    client.post("/papers/new", data={"title": "Consent Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Consent Paper").one().slug

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "model.stl")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "must confirm" in response.get_data(as_text=True)
    with client.application.app_context():
        assert Model3D.query.count() == 0


def test_upload_rate_limit_blocks_repeated_attempts(client):
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    client.application.config["UPLOAD_RATE_LIMIT_COUNT"] = 1
    client.application.config["UPLOAD_RATE_LIMIT_WINDOW"] = 600
    register(client)
    client.post("/papers/new", data={"title": "Rate Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Rate Paper").one().slug

    first = client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "first.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    second = client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "second.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert "Too many upload attempts" in second.get_data(as_text=True)


def test_pilot_requires_real_secret_key():
    from app import create_app

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(
            {
                "APP_ENV": "pilot",
                "SECRET_KEY": "dev-secret-change-in-production",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            }
        )
