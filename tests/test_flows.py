from datetime import UTC, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

from models import AuditLog, ConversionJob, Model3D, ModelVersion, Paper, Payment, QRLink, User, db
from models import utc_now


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


def test_landing_mitochondria_qr_and_ar_page(client):
    landing = client.get("/")
    assert landing.status_code == 200
    assert "/demo/mitochondria/qr.png" in landing.get_data(as_text=True)
    assert "/demo/mitochondria/ar" in landing.get_data(as_text=True)

    qr = client.get("/demo/mitochondria/qr.png")
    assert qr.status_code == 200
    assert qr.mimetype == "image/png"

    ar_page = client.get("/demo/mitochondria/ar")
    assert ar_page.status_code == 200
    assert "Mitochondria AR" in ar_page.get_data(as_text=True)
    assert "activateAR" in ar_page.get_data(as_text=True)


def test_paper_create_can_include_first_model(client):
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    register(client)
    response = client.post(
        "/papers/new",
        data={
            "title": "Paper With First Model",
            "year": "2026",
            "field": "Medicine",
            "model_file": upload_file_bytes(valid_ascii_stl_bytes(), "first.stl"),
            "model_display_name": "First model",
            "license_type": "academic",
            "compliance_confirm": "yes",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        paper = Paper.query.filter_by(title="Paper With First Model").one()
        model = Model3D.query.filter_by(paper_id=paper.id).one()
        assert model.display_name == "First model"
        assert model.license_type == "academic"
        assert model.processing_status == "ready"
        assert model.qr_code_path
        assert ConversionJob.query.filter_by(model_id=model.id, status="completed").one()
        assert ModelVersion.query.filter_by(model_id=model.id, version_number=1, status="ready").one()


def test_authenticated_user_can_view_landing_page(client):
    from tests.conftest import login, register

    register(client)
    login(client)

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "AcademicAR" in response.get_data(as_text=True)


def test_google_auth_buttons_explain_when_unconfigured(client):
    login_response = client.get("/auth/login")
    register_response = client.get("/auth/register")

    assert login_response.status_code == 200
    assert "Google sign-in will be available" in login_response.get_data(as_text=True)
    assert register_response.status_code == 200
    assert "Google sign-up will be available" in register_response.get_data(as_text=True)


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
        assert model.public_id
        assert QRLink.query.filter_by(model_id=model.id, public_id=model.public_id).one()
        assert ConversionJob.query.filter_by(model_id=model.id, status="completed").one()
        assert ModelVersion.query.filter_by(model_id=model.id, version_number=1, status="ready").one()


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


def test_managed_qr_resolver_uses_stable_public_id(client):
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    register(client)
    client.post("/papers/new", data={"title": "Resolver Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Resolver Paper").one().slug

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "resolver.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = Model3D.query.one()
        public_id = model.public_id
        model_id = model.id

    response = client.get(f"/m/{public_id}", follow_redirects=False)
    assert response.status_code == 302
    assert f"/view/{model_id}" in response.headers["Location"]


def test_model_license_upgrade_and_replace_keep_public_id(client):
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    register(client)
    client.post("/papers/new", data={"title": "Replace Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Replace Paper").one().slug

    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "first.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with client.application.app_context():
        model = Model3D.query.one()
        public_id = model.public_id
        model_id = model.id

    upgrade = client.post(
        f"/models/{model_id}/license",
        data={"license_type": "academic"},
        follow_redirects=True,
    )
    assert upgrade.status_code == 200

    replace = client.post(
        f"/models/{model_id}/replace",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "second.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert replace.status_code == 200

    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.public_id == public_id
        assert model.license_type == "academic"
        assert model.original_filename == "second.stl"
        assert model.version == 2
        assert QRLink.query.filter_by(model_id=model_id, public_id=public_id).one()
        assert ModelVersion.query.filter_by(model_id=model_id, version_number=2, status="ready").one()


def test_free_model_expires_after_three_days_but_keeps_qr_and_can_be_upgraded(client):
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    register(client)
    client.post("/papers/new", data={"title": "Free Expiry Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Free Expiry Paper").one().slug

    client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": upload_file_bytes(valid_ascii_stl_bytes(), "free.stl"),
            "license_type": "free",
            "compliance_confirm": "yes",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    with client.application.app_context():
        model = Model3D.query.one()
        model_id = model.id
        public_id = model.public_id
        qr_code_path = model.qr_code_path
        starts_at = model.access_starts_at
        expires_at = model.access_expires_at
        assert model.license_type == "free"
        assert starts_at is not None
        assert expires_at is not None
        assert expires_at.replace(tzinfo=UTC) == starts_at.replace(tzinfo=UTC) + timedelta(days=3)

    assert client.get(f"/m/{public_id}", follow_redirects=False).status_code == 302
    assert client.get(f"/view/{model_id}").status_code == 200
    assert client.get(f"/files/{model_id}/model.glb").status_code == 200
    assert client.get(f"/qr-image/{model_id}").status_code == 200

    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        model.access_expires_at = utc_now() - timedelta(seconds=1)
        db.session.commit()

    expired_resolver = client.get(f"/m/{public_id}", follow_redirects=False)
    expired_viewer = client.get(f"/view/{model_id}")
    assert expired_resolver.status_code == 410
    assert expired_viewer.status_code == 410
    assert "This model link has expired." in expired_viewer.get_data(as_text=True)
    assert client.get(f"/files/{model_id}/model.glb").status_code == 404

    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.public_id == public_id
        assert model.qr_code_path == qr_code_path
        assert QRLink.query.filter_by(model_id=model_id, public_id=public_id).one()

    upgrade = client.post(
        f"/models/{model_id}/license",
        data={"license_type": "academic"},
        follow_redirects=True,
    )
    assert upgrade.status_code == 200

    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.license_type == "academic"
        assert model.public_id == public_id
        assert model.qr_code_path == qr_code_path
        assert model.access_expires_at.replace(tzinfo=UTC) == starts_at.replace(tzinfo=UTC) + timedelta(days=365 * 3)

    assert client.get(f"/m/{public_id}", follow_redirects=False).status_code == 302
    assert client.get(f"/view/{model_id}").status_code == 200
    assert client.get(f"/files/{model_id}/model.glb").status_code == 200


def test_model_appearance_update_keeps_public_id(client, monkeypatch):
    import app as app_module
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    monkeypatch.setattr(app_module, "enrich_glb_for_ar", lambda *args, **kwargs: True)

    register(client)
    client.post("/papers/new", data={"title": "Color Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Color Paper").one().slug

    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "color.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with client.application.app_context():
        model = Model3D.query.one()
        public_id = model.public_id
        model_id = model.id

    response = client.post(
        f"/models/{model_id}/appearance",
        data={"color": "#aabbcc"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.appearance_color == "#aabbcc"
        assert model.public_id == public_id

    response = client.post(
        f"/models/{model_id}/appearance",
        data={"color": "#aabbcc", "color_command": "make it light gray"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.appearance_color == "#d9d9d9"
        assert model.public_id == public_id


def test_failed_replacement_preserves_previous_working_glb(client, monkeypatch):
    import app as app_module
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    register(client)
    client.post("/papers/new", data={"title": "Stable Replace Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Stable Replace Paper").one().slug

    initial_glb = b"glTF" + b"\x00" * 24
    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(initial_glb, "initial.glb"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with client.application.app_context():
        model = Model3D.query.one()
        model_id = model.id
        public_id = model.public_id
        glb_path = Path(model.glb_path)
        assert glb_path.read_bytes() == initial_glb

    class FailingConverter:
        errors = ["forced conversion failure"]

        def convert(self, *args, **kwargs):
            return False

    monkeypatch.setattr(app_module, "STLConverter", FailingConverter)

    response = client.post(
        f"/models/{model_id}/replace",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "replacement.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.public_id == public_id
        assert model.processing_status == "replacement_failed"
        assert Path(model.glb_path).read_bytes() == initial_glb
        assert ConversionJob.query.filter_by(model_id=model_id, status="failed").one()
        assert ModelVersion.query.filter_by(model_id=model_id, version_number=2, status="failed").one()

    assert client.get(f"/files/{model_id}/model.glb").status_code == 200
    assert client.get(f"/m/{public_id}", follow_redirects=False).status_code == 302


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


def test_admin_dashboard_requires_admin_user(client):
    from tests.conftest import create_user, login

    with client.application.app_context():
        create_user()

    login(client)
    assert client.get("/admin").status_code == 403

    with client.application.app_context():
        user = User.query.filter_by(email="user@example.com").one()
        user.is_admin = True
        db.session.commit()

    response = client.get("/admin")
    assert response.status_code == 200
    assert "AcademicAR control panel" in response.get_data(as_text=True)
    assert "Operations overview" in response.get_data(as_text=True)

    expected_pages = {
        "/admin/content": "Content and publication statistics",
        "/admin/models": "Model and conversion health",
        "/admin/access": "QR and viewer analytics",
        "/admin/revenue": "License and revenue",
        "/admin/security": "Operations and security",
        "/admin/storage": "Storage",
        "/admin/users": "Users",
        "/admin/logs": "Audit log",
        "/admin/backups": "Backups",
    }
    for path, marker in expected_pages.items():
        response = client.get(path)
        assert response.status_code == 200
        assert marker in response.get_data(as_text=True)


def test_configured_admin_email_is_always_admin(client):
    from tests.conftest import create_user, login

    with client.application.app_context():
        user = create_user(email="melikhanmutlu@gmail.com", username="Melikhan Mutlu")
        assert user.is_admin is False

    login(client, email="melikhanmutlu@gmail.com")
    response = client.get("/admin")
    assert response.status_code == 200
    assert "AcademicAR control panel" in response.get_data(as_text=True)

    with client.application.app_context():
        user = User.query.filter_by(email="melikhanmutlu@gmail.com").one()
        assert user.is_admin is True


def test_admin_can_update_user_plan_and_role(client):
    from tests.conftest import create_user, login

    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin User")
        admin.is_admin = True
        member = create_user(email="member@example.com", username="Member User")
        db.session.commit()
        member_id = member.id

    login(client, email="admin@example.com")
    response = client.post(
        f"/admin/users/{member_id}/plan",
        data={"plan": "academic"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Plan updated" in response.get_data(as_text=True)

    response = client.post(
        f"/admin/users/{member_id}/role",
        data={"is_admin": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Admin access updated" in response.get_data(as_text=True)

    with client.application.app_context():
        member = db.session.get(User, member_id)
        assert member.plan == "academic"
        assert member.is_admin is True


def test_admin_user_detail_dashboard_and_backup(client):
    from tests.conftest import create_user, login

    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin User")
        admin.is_admin = True
        member = create_user(email="member@example.com", username="Member User")
        paper = Paper(title="Member Paper", slug="member-paper", user_id=member.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="member-model-1",
            paper_id=paper.id,
            user_id=member.id,
            display_name="Member Model",
            glb_path="model.glb",
            public_id="member-public",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.add(QRLink(public_id="member-public", model_id=model.id))
        db.session.commit()
        member_id = member.id

    login(client, email="admin@example.com")

    detail = client.get(f"/admin/users/{member_id}")
    assert detail.status_code == 200
    detail_text = detail.get_data(as_text=True)
    assert "Member User" in detail_text
    assert "Member Paper" in detail_text
    assert "Member Model" in detail_text

    dashboard = client.get(f"/admin/users/{member_id}/dashboard")
    assert dashboard.status_code == 200
    assert "Member User&#39;s dashboard" in dashboard.get_data(as_text=True) or "Member User's dashboard" in dashboard.get_data(as_text=True)

    backup = client.post("/admin/backups/create", follow_redirects=True)
    assert backup.status_code == 200
    assert "Backup created" in backup.get_data(as_text=True)
    with client.application.app_context():
        assert AuditLog.query.filter_by(event_type="admin_backup_created").count() >= 1


def test_admin_can_operate_publication_model_qr_and_payment(client):
    from tests.conftest import create_user, login

    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin User")
        admin.is_admin = True
        owner = create_user(email="owner@example.com", username="Owner User")
        paper = Paper(title="Admin Ops Paper", slug="admin-ops-paper", user_id=owner.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="admin-model-1",
            paper_id=paper.id,
            user_id=owner.id,
            glb_path="model.glb",
            file_size=1024,
            public_id="pub-admin-1",
            processing_status="ready",
        )
        db.session.add(model)
        qr = QRLink(public_id="pub-admin-1", model_id=model.id)
        payment = Payment(user_id=owner.id, amount_kurus=99000, status="pending")
        db.session.add_all([qr, payment])
        db.session.commit()
        paper_id = paper.id
        qr_id = qr.id
        payment_id = payment.id

    login(client, email="admin@example.com")

    assert client.post(
        f"/admin/papers/{paper_id}/visibility",
        data={"is_public": "1", "status": "active"},
        follow_redirects=True,
    ).status_code == 200
    assert client.post(
        "/admin/models/admin-model-1/license",
        data={"license_type": "academic"},
        follow_redirects=True,
    ).status_code == 200
    assert client.post(
        "/admin/models/admin-model-1/processing",
        data={"processing_status": "failed"},
        follow_redirects=True,
    ).status_code == 200
    assert client.post(
        f"/admin/qr-links/{qr_id}/status",
        data={"status": "disabled"},
        follow_redirects=True,
    ).status_code == 200
    assert client.post(
        f"/admin/payments/{payment_id}/status",
        data={"status": "paid"},
        follow_redirects=True,
    ).status_code == 200

    with client.application.app_context():
        paper = db.session.get(Paper, paper_id)
        model = db.session.get(Model3D, "admin-model-1")
        qr = db.session.get(QRLink, qr_id)
        payment = db.session.get(Payment, payment_id)
        assert paper.is_public is True
        assert model.license_type == "academic"
        assert model.processing_status == "failed"
        assert qr.status == "disabled"
        assert payment.status == "paid"
        assert payment.paid_at is not None


def test_public_viewer_and_qr_resolver_feed_admin_analytics(client):
    with client.application.app_context():
        owner = User(email="owner@example.com", username="Owner User")
        owner.set_password("password123")
        db.session.add(owner)
        db.session.flush()
        paper = Paper(title="Analytics Paper", slug="analytics-paper", user_id=owner.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="analytics-model-1",
            paper_id=paper.id,
            user_id=owner.id,
            glb_path="model.glb",
            public_id="analytics-public",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.add(QRLink(public_id="analytics-public", model_id=model.id))
        db.session.commit()

    assert client.get("/view/analytics-model-1").status_code == 200
    assert client.get("/m/analytics-public", follow_redirects=False).status_code == 302

    with client.application.app_context():
        assert QRLink.query.filter_by(public_id="analytics-public").one().last_resolved_at is not None
        assert AuditLog.query.filter_by(event_type="public_model_viewed", resource_id="analytics-model-1").count() == 1
        assert AuditLog.query.filter_by(event_type="qr_resolved", resource_id="analytics-public").count() == 1


def test_obj_upload_with_companion_files_archives_mtl_and_textures(client, monkeypatch):
    """OBJ upload with .mtl and .png companion files archives all files into
    the versioned sources directory so obj2gltf can resolve them."""
    import app as app_module
    import subprocess
    from tests.conftest import register, upload_file_bytes

    register(client)
    client.post("/papers/new", data={"title": "OBJ Companion Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="OBJ Companion Paper").one().slug

    obj_content = b"# OBJ\nmtllib mesh.mtl\no mesh\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
    mtl_content = b"newmtl default\nKd 1.0 0.0 0.0\n"
    texture_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # fake PNG header

    # Monkey-patch the OBJ converter to simulate a successful conversion
    from converters.external_converter import OBJConverter

    def fake_run(self_converter, command, cwd):
        # Verify companion files are accessible from the converter's CWD
        import os
        assert os.path.exists(os.path.join(cwd, "mesh.mtl")), "MTL file not found in converter CWD"
        # Create the output GLB so the pipeline considers conversion successful
        output_path = None
        for i, arg in enumerate(command):
            if arg == "-o" and i + 1 < len(command):
                output_path = command[i + 1]
                break
        if output_path:
            with open(output_path, "wb") as f:
                f.write(b"glTF" + b"\x00" * 24)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(OBJConverter, "_run", fake_run)

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": upload_file_bytes(obj_content, "mesh.obj"),
            "companion_files": [
                upload_file_bytes(mtl_content, "mesh.mtl"),
                upload_file_bytes(texture_content, "texture.png"),
            ],
            "compliance_confirm": "yes",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = Model3D.query.one()
        assert model.source_format == "obj"
        assert model.processing_status == "ready"
        assert model.qr_code_path

        # Verify archived sources directory contains OBJ + MTL + texture
        from pathlib import Path

        source_dir = Path(model.original_source_path).parent
        archived_files = {p.name for p in source_dir.iterdir()}
        assert "mesh.obj" in archived_files
        assert "mesh.mtl" in archived_files
        assert "texture.png" in archived_files

        # Verify conversion job completed
        assert ConversionJob.query.filter_by(model_id=model.id, status="completed").one()


def test_appearance_update_failure_preserves_previous_glb(client, monkeypatch):
    """When appearance update fails mid-flight, the previous working GLB
    is restored from the backup (MVP Section 9 requirement)."""
    import app as app_module
    from tests.conftest import register, upload_file_bytes

    register(client)
    client.post("/papers/new", data={"title": "Appearance Safety Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Appearance Safety Paper").one().slug

    initial_glb = b"glTF" + b"\x00" * 24
    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(initial_glb, "safe.glb"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with client.application.app_context():
        model = Model3D.query.one()
        model_id = model.id
        glb_path = Path(model.glb_path)
        assert glb_path.read_bytes() == initial_glb

    # Make enrich_glb_for_ar raise an exception to simulate a mid-flight failure
    def exploding_enrich(*args, **kwargs):
        # Corrupt the GLB first to simulate partial write
        with open(args[0], "wb") as f:
            f.write(b"CORRUPTED")
        raise RuntimeError("Simulated enrichment failure")

    monkeypatch.setattr(app_module, "enrich_glb_for_ar", exploding_enrich)

    response = client.post(
        f"/models/{model_id}/appearance",
        data={"color": "#ff0000"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "could not be updated" in response.get_data(as_text=True)

    # The GLB must be restored to its original content
    assert glb_path.read_bytes() == initial_glb
    # No leftover backup file
    assert not Path(str(glb_path) + ".appearance_backup").exists()


def test_obj_without_textures_can_receive_color(client, monkeypatch):
    """When an OBJ is uploaded without companion files, a requested solid color is applied."""
    import subprocess
    from converters.external_converter import OBJConverter
    from tests.conftest import register, upload_file_bytes

    register(client)
    client.post("/papers/new", data={"title": "OBJ Color Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="OBJ Color Paper").one().slug

    obj_content = b"o mesh\nv 0 0 0\n"

    # Simulate conversion that just creates an empty GLB so the color injector can run
    def fake_run(self_converter, command, cwd):
        output_path = None
        for i, arg in enumerate(command):
            if arg == "-o" and i + 1 < len(command):
                output_path = command[i + 1]
                break
        if output_path:
            with open(output_path, "wb") as f:
                f.write(b"glTF" + b"\x00" * 24)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(OBJConverter, "_run", fake_run)

    # We also mock enrich_glb_for_ar to prevent it from failing on our fake GLB
    import converters.external_converter as ext_conv
    monkeypatch.setattr(ext_conv, "enrich_glb_for_ar", lambda path, rgba: True)

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": upload_file_bytes(obj_content, "mesh.obj"),
            "color": "#ff0000",
            "compliance_confirm": "yes",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = Model3D.query.one()
        assert model.source_format == "obj"
        assert model.appearance_color == "#ff0000"
        assert model.processing_status == "ready"


def test_fbx_upload_converts_to_glb(client, monkeypatch):
    """FBX upload correctly passes through the FBXConverter pipeline."""
    import subprocess
    from converters.external_converter import FBXConverter
    from tests.conftest import register, upload_file_bytes

    register(client)
    client.post("/papers/new", data={"title": "FBX Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="FBX Paper").one().slug

    fbx_content = b"Kaydara FBX Binary\x20\x20\x00\x1a\x00"

    def fake_run(self_converter, command, cwd):
        output_path = None
        for i, arg in enumerate(command):
            if arg == "-o" and i + 1 < len(command):
                output_path = command[i + 1]
                break
        if output_path:
            with open(output_path, "wb") as f:
                f.write(b"glTF" + b"\x00" * 24)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(FBXConverter, "_run", fake_run)

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": upload_file_bytes(fbx_content, "mesh.fbx"),
            "compliance_confirm": "yes",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = Model3D.query.filter_by(source_format="fbx").one()
        assert model.processing_status == "ready"
        assert ConversionJob.query.filter_by(model_id=model.id, status="completed").one()


def test_missing_fbx2gltf_produces_clear_error(client, monkeypatch):
    """When FBX2glTF CLI is missing or fails, a user-facing error is recorded on the model."""
    import subprocess
    from converters.external_converter import FBXConverter
    from tests.conftest import register, upload_file_bytes

    register(client)
    client.post("/papers/new", data={"title": "Missing Tool Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Missing Tool Paper").one().slug

    fbx_content = b"Kaydara FBX Binary\x20\x20\x00\x1a\x00"

    def fake_run(self_converter, command, cwd):
        # Simulate missing executable or crash
        return subprocess.CompletedProcess(command, 127, "", "fbx2gltf: command not found")

    monkeypatch.setattr(FBXConverter, "_run", fake_run)

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": upload_file_bytes(fbx_content, "mesh.fbx"),
            "compliance_confirm": "yes",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = Model3D.query.filter_by(source_format="fbx").one()
        assert model.processing_status == "failed"
        assert "fbx2gltf: command not found" in model.processing_error
        assert ConversionJob.query.filter_by(model_id=model.id, status="failed").one()


def test_production_upload_only_enqueues_conversion_job(client, monkeypatch):
    """Production web requests must not run conversion work inline."""
    import app as app_module
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    register(client)
    client.post("/papers/new", data={"title": "Worker Boundary Paper"}, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title="Worker Boundary Paper").one().slug
        client.application.config["TESTING"] = False
        client.application.config["DEV_INLINE_JOBS"] = False

    def fail_if_called(*args, **kwargs):
        raise AssertionError("web request ran conversion inline")

    monkeypatch.setattr(app_module, "process_model_upload_job", fail_if_called)

    response = client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "queued.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with client.application.app_context():
        model = Model3D.query.filter_by(source_format="stl").one()
        assert model.processing_status == "queued"
        assert ConversionJob.query.filter_by(model_id=model.id, status="pending").one()


def test_upload_rate_limit_is_enforced_across_requests(client):
    from tests.conftest import register, upload_file_bytes, valid_ascii_stl_bytes

    client.application.config["UPLOAD_RATE_LIMIT_COUNT"] = 1
    client.application.config["UPLOAD_RATE_LIMIT_WINDOW"] = 60

    register(client)
    client.post("/papers/new", data={"title": "Rate Paper One"}, follow_redirects=True)
    client.post("/papers/new", data={"title": "Rate Paper Two"}, follow_redirects=True)
    with client.application.app_context():
        slug_one = Paper.query.filter_by(title="Rate Paper One").one().slug
        slug_two = Paper.query.filter_by(title="Rate Paper Two").one().slug

    first = client.post(
        f"/papers/{slug_one}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "one.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert first.status_code == 200

    second = client.post(
        f"/papers/{slug_two}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "two.stl"), "compliance_confirm": "yes"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert second.status_code == 200
    assert "Too many upload attempts" in second.get_data(as_text=True)

    with client.application.app_context():
        assert Model3D.query.count() == 1
        assert ConversionJob.query.count() == 1


def test_papers_fetch_metadata_endpoint(client, monkeypatch):
    import urllib.request
    from tests.conftest import register
    
    register(client)
    
    # 1. Test empty query
    response = client.post("/papers/fetch-metadata", json={"query": ""})
    assert response.status_code == 400
    assert "cannot be empty" in response.get_json()["error"]
    
    # 2. Test invalid query format
    response = client.post("/papers/fetch-metadata", json={"query": "invalid_query_format"})
    assert response.status_code == 400
    assert "Please enter a valid DOI" in response.get_json()["error"]
    
    # 3. Test mock DOI success response
    class FakeResponse:
        def __init__(self, data, status=200):
            self.data = data
            self.status = status
        def read(self):
            return self.data
        def decode(self, encoding):
            return self.data.decode(encoding)
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc_value, traceback):
            pass
            
    def fake_urlopen_doi(req, timeout=5):
        assert "api.crossref.org" in req.full_url
        mock_data = {
            "message": {
                "title": ["3D Visual Reconstruction of Lung Cells"],
                "author": [
                    {"given": "Jane", "family": "Doe"},
                    {"given": "John", "family": "Smith"}
                ],
                "created": {
                    "date-parts": [[2026, 5, 29]]
                },
                "abstract": "<p>A detailed 3D anatomy study.</p>",
                "publisher": "Radiology Journal",
                "container-title": ["Journal of Medical Imaging"]
            }
        }
        import json
        return FakeResponse(json.dumps(mock_data).encode("utf-8"))
        
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_doi)
    
    response = client.post("/papers/fetch-metadata", json={"query": "10.1148/radiol.210408"})
    assert response.status_code == 200
    res_json = response.get_json()
    assert res_json["success"] is True
    assert res_json["title"] == "3D Visual Reconstruction of Lung Cells"
    assert "Doe Jane" in res_json["authors"]
    assert "Smith John" in res_json["authors"]
    assert res_json["year"] == 2026
    assert res_json["abstract"] == "A detailed 3D anatomy study."
    assert res_json["institution"] == "Journal of Medical Imaging"
    assert res_json["doi"] == "10.1148/radiol.210408"
    
    # 4. Test mock PMID success response
    def fake_urlopen_pmid(req, timeout=5):
        assert "eutils.ncbi.nlm.nih.gov" in req.full_url
        mock_data = {
            "result": {
                "34567890": {
                    "title": "A 3D heart mapping report",
                    "authors": [
                        {"name": "Doe J"},
                        {"name": "Smith J"}
                    ],
                    "pubdate": "2026 May 15",
                    "source": "Nature Medicine",
                    "articleids": [
                        {"idtype": "doi", "value": "10.1038/nm.3456"}
                    ]
                }
            }
        }
        import json
        return FakeResponse(json.dumps(mock_data).encode("utf-8"))
        
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen_pmid)
    
    response = client.post("/papers/fetch-metadata", json={"query": "34567890"})
    assert response.status_code == 200
    res_json = response.get_json()
    assert res_json["success"] is True
    assert res_json["title"] == "A 3D heart mapping report"
    assert "Doe J" in res_json["authors"]
    assert "Smith J" in res_json["authors"]
    assert res_json["year"] == 2026
    assert res_json["institution"] == "Nature Medicine"
    assert res_json["doi"] == "10.1038/nm.3456"
    assert res_json["pmid"] == "34567890"
