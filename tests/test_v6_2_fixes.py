"""Regression tests for the v6.2 third audit fixes.

Covers: account deletion FK anonymization, model_delete null paper guard,
external converter timeout handling, QR image deleted-paper check.
"""
from models import AuditLog, ConversionJob, Model3D, Paper, Payment, User, db


def test_account_delete_anonymizes_fk_references(client):
    """Deleting an account should NULL out user_id on Payment, AuditLog,
    ConversionJob rows instead of violating FK constraints."""
    from tests.conftest import login, register

    register(client, email="fktest@example.com")
    app = client.application
    with app.app_context():
        user = User.query.filter_by(email="fktest@example.com").one()
        uid = user.id
        paper = Paper(title="FK Paper", slug="fk-paper", user_id=uid)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="fk-model-test",
            paper_id=paper.id,
            user_id=uid,
            glb_path="m.glb",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.flush()
        payment = Payment(user_id=uid, paper_id=paper.id, amount_kurus=990, currency="TRY")
        db.session.add(payment)
        audit = AuditLog(event_type="test_event", user_id=uid)
        db.session.add(audit)
        job = ConversionJob(
            job_type="model_upload",
            status="completed",
            model_id=model.id,
            user_id=uid,
        )
        db.session.add(job)
        db.session.commit()
        payment_id = payment.id
        audit_id = audit.id
        job_id = job.id

    login(client, email="fktest@example.com")
    resp = client.post(
        "/account/delete",
        data={"confirm": "DELETE", "current_password": "password123"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        assert User.query.filter_by(email="fktest@example.com").first() is None
        p = db.session.get(Payment, payment_id)
        assert p is not None
        assert p.user_id is None
        a = db.session.get(AuditLog, audit_id)
        assert a is not None
        assert a.user_id is None
        j = db.session.get(ConversionJob, job_id)
        if j:
            assert j.user_id is None


def test_model_delete_orphan_model_returns_404(client):
    """model_delete should 404 when the model's paper is gone."""
    from tests.conftest import login, register

    register(client, email="orphan-del@example.com")
    app = client.application
    with app.app_context():
        user = User.query.filter_by(email="orphan-del@example.com").one()
        paper = Paper(title="Temp", slug="temp-del", user_id=user.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="orphan-del-test",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="m.glb",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()
        db.session.delete(paper)
        db.session.commit()

    login(client, email="orphan-del@example.com")
    resp = client.post("/models/orphan-del-test/delete", follow_redirects=True)
    assert resp.status_code == 404


def test_external_converter_bad_timeout_env(monkeypatch):
    """Non-numeric MODEL_CONVERT_TIMEOUT should not crash the converter."""
    from converters.external_converter import _safe_timeout

    monkeypatch.setenv("MODEL_CONVERT_TIMEOUT", "5m")
    assert _safe_timeout() == 300

    monkeypatch.setenv("MODEL_CONVERT_TIMEOUT", "120")
    assert _safe_timeout() == 120


def test_qr_image_deleted_paper_returns_404(client):
    """QR image should 404 when the paper was soft-deleted (papers never
    expire on their own; per-model access windows gate the viewer instead)."""
    from tests.conftest import register

    register(client, email="qrexp@example.com")
    app = client.application
    with app.app_context():
        user = User.query.filter_by(email="qrexp@example.com").one()
        paper = Paper(
            title="Deleted Paper",
            slug="deleted-paper",
            user_id=user.id,
            status="deleted",
        )
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="deleted-qr-image",
            paper_id=paper.id,
            user_id=user.id,
            glb_path="model.glb",
            qr_code_path="qr.png",
            processing_status="ready",
        )
        db.session.add(model)
        db.session.commit()

    resp = client.get("/qr-image/deleted-qr-image")
    assert resp.status_code == 404
