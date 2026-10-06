from datetime import UTC, datetime

from models import ConversionJob, Model3D, Paper, db
from tests.conftest import create_user, login


def _admin(client):
    with client.application.app_context():
        admin = create_user(email="admin@example.com", username="Admin")
        admin.is_admin = True
        db.session.commit()
    login(client, email="admin@example.com")


def _seed(client):
    with client.application.app_context():
        owner = create_user(email="o@example.com", username="Owner")
        paper = Paper(title="P", slug="p", user_id=owner.id)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(
            id="job-model-uuid-0001", paper_id=paper.id, user_id=owner.id, glb_path="m.glb",
            display_name="Skull Scan", processing_status="ready",
        )
        db.session.add(model)
        for jt, st, err in (
            ("model_upload", "failed", "E" * 300),
            ("scene_ar", "pending", None),
            ("poster", "completed", None),
        ):
            db.session.add(ConversionJob(
                job_type=jt, status=st, model_id=model.id, attempts=1, error=err,
                payload={"k": "v"}, finished_at=datetime.now(UTC),
            ))
        db.session.commit()


def test_failed_messages_section_removed(client):
    _admin(client)
    _seed(client)
    text = client.get("/admin/jobs").get_data(as_text=True)
    assert "Failed job messages" not in text
    assert 'id="failed-jobs"' not in text
    assert "3 jobs" in text


def test_job_type_filter_and_labels(client):
    _admin(client)
    _seed(client)
    text = client.get("/admin/jobs?job_type=scene_ar").get_data(as_text=True)
    assert "1 job<" in text
    assert "<strong>Scene AR</strong>" in text
    assert "<strong>Model upload</strong>" not in text
    text = client.get("/admin/jobs?job_type=bogus").get_data(as_text=True)
    assert "No jobs match these filters." in text


def test_error_truncated_model_name_shown_no_inline_style(client):
    _admin(client)
    _seed(client)
    text = client.get("/admin/jobs?job_status=failed").get_data(as_text=True)
    assert 'title="' + "E" * 300 + '"' in text
    cell = text.split('jobs-error"')[1].split("</td>")[0]
    visible = cell.split('">', 1)[1]
    assert visible.endswith("...") and len(visible) < 100
    assert "Skull Scan" in text
    assert "<small>job-model-uuid-0001</small>" not in text
    section = text.split('id="jobs"')[1].split("</section>")[0]
    assert 'style="' not in section
