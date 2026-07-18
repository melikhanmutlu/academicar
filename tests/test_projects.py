"""v26 project-first workflow and reviewer sharing regression tests."""
from models import Paper


def test_project_can_be_created_without_publication_metadata(client):
    from tests.conftest import login, register

    register(client)
    login(client)
    response = client.post(
        "/projects/new",
        data={
            "title": "Prototype femur reconstruction",
            "project_type": "research_project",
            "workflow_stage": "in_progress",
            "visibility": "private",
            "abstract": "A pre-publication model review workspace.",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Project created." in response.get_data(as_text=True)

    with client.application.app_context():
        project = Paper.query.filter_by(title="Prototype femur reconstruction").one()
        assert project.project_type == "research_project"
        assert project.workflow_stage == "in_progress"
        assert project.visibility == "private"
        assert project.doi is None


def test_unlisted_project_uses_opaque_reviewer_link(client):
    from tests.conftest import login, register

    register(client)
    login(client)
    response = client.post(
        "/projects/new",
        data={
            "title": "Reviewer-only scan set",
            "project_type": "research_project",
            "workflow_stage": "ready_for_review",
            "visibility": "unlisted",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    with client.application.app_context():
        project = Paper.query.filter_by(title="Reviewer-only scan set").one()
        assert project.share_token
        slug, token = project.slug, project.share_token

    # A title-derived public URL must not expose an unlisted review project.
    anonymous = client.application.test_client()
    assert anonymous.get(f"/p/{slug}").status_code == 404
    review = anonymous.get(f"/share/{token}")
    assert review.status_code == 200
    assert review.headers["X-Robots-Tag"] == "noindex, nofollow, noarchive"
