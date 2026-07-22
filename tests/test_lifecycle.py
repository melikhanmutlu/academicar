"""Tests for individual paid-model license renewal reminders (worker path)."""
import uuid
from datetime import UTC, datetime, timedelta


def _paid_model(app, *, days_to_expiry, email="owner-renew@example.com"):
    from models import Model3D, Paper, User, db

    with app.app_context():
        user = User(email=email, username="Renew Owner")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        paper = Paper(title="Renew Paper", slug=f"renew-{uuid.uuid4().hex[:6]}", user_id=user.id, is_public=True)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id=str(uuid.uuid4()),
            paper_id=paper.id,
            user_id=user.id,
            display_name="Renewable model",
            original_filename="renew.glb",
            glb_path="renew.glb",
            processing_status="ready",
            license_type="academic",
            access_starts_at=datetime.now(UTC).replace(tzinfo=None),
            access_expires_at=(datetime.now(UTC) + timedelta(days=days_to_expiry)).replace(tzinfo=None),
        )
        db.session.add(model)
        db.session.commit()
        return user.id, model.id


def test_renewal_reminder_sent_and_deduped(app, monkeypatch):
    from utils import email as email_mod
    import lifecycle
    from models import AuditLog

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(email_mod, "send_email", lambda to, subject, body: sent.append((to, subject)) or True)

    _paid_model(app, days_to_expiry=5)  # inside the 7-day window
    with app.app_context():
        assert lifecycle.send_model_renewal_reminders() == 1
        assert any(to == "owner-renew@example.com" for to, _ in sent)
        assert AuditLog.query.filter_by(event_type=lifecycle.RENEWAL_EVENT).count() == 1

        # Same window/expiry again -> deduped.
        sent.clear()
        assert lifecycle.send_model_renewal_reminders() == 0
        assert sent == []


def test_renewal_reminder_rearms_on_new_window(app, monkeypatch):
    from utils import email as email_mod
    import lifecycle

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(email_mod, "send_email", lambda to, subject, body: sent.append((to, subject)) or True)

    # Expires in 20 days -> only the 30-day bucket fires now.
    _, model_id = _paid_model(app, days_to_expiry=20)
    with app.app_context():
        assert lifecycle.send_model_renewal_reminders() == 1  # 30-day bucket

    # Simulate time passing to 5 days before expiry -> 7-day bucket re-fires.
    later = datetime.now(UTC) + timedelta(days=15)
    with app.app_context():
        assert lifecycle.send_model_renewal_reminders(now=later) == 1  # 7-day bucket


def test_free_and_far_models_are_not_reminded(app, monkeypatch):
    from utils import email as email_mod
    import lifecycle
    from models import Model3D, db

    monkeypatch.setattr(email_mod, "send_email", lambda *a, **k: True)

    # Paid but far from expiry (90 days) -> outside the 30-day window.
    _paid_model(app, days_to_expiry=90, email="far@example.com")
    # Free model expiring soon -> not a paid renewal target.
    _, free_id = _paid_model(app, days_to_expiry=2, email="free@example.com")
    with app.app_context():
        db.session.get(Model3D, free_id).license_type = "free"
        db.session.commit()
        assert lifecycle.send_model_renewal_reminders() == 0
