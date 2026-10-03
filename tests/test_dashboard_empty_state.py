"""First-run dashboard: one message and one clear "New Project" action."""
from tests.conftest import register


def test_empty_dashboard_is_minimal_with_new_project_button(client):
    register(client)
    html = client.get("/dashboard").data.decode()
    assert "Create your first project" in html
    assert 'class="lp-btn lp-btn-dark dashboard-empty-cta"' in html
    assert 'href="/projects/new"' in html
    # The old dark panel listed fake demo publications next to real data.
    assert "Mitochondrial morphology atlas" not in html
    assert "Demo publication" not in html
