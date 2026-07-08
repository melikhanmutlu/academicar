"""Public demo dashboard pages (no login): researcher dashboard + institution
panel, rendered from the real templates with fabricated sample data."""


def test_demo_dashboard_public_and_populated(client):
    response = client.get("/demo/dashboard")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # Sample publications render
    assert "Cellular Organelle Morphology in Human Hepatocytes" in body
    # Demo banner + register CTA
    assert "This is a live example." in body
    assert "/auth/register" in body
    # Cross-link to the institution demo
    assert "/demo/institution" in body


def test_demo_dashboard_actions_do_not_point_to_protected_routes(client):
    body = client.get("/demo/dashboard").get_data(as_text=True)
    # Authoring routes must not appear as action targets (they'd redirect to login)
    assert "/papers/new" not in body
    assert "/papers/demo-publication/edit" not in body
    # Publication title/Details resolve to the sign-up CTA, not the model viewer
    assert "/demo/mitochondria/ar" not in body
    assert "/auth/register" in body


def test_demo_institution_public_and_populated(client):
    response = client.get("/demo/institution")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Bogazici University" in body
    # Quota usage + contract surfaced
    assert "Funded models" in body
    assert "Quota usage" in body
    assert "This is a live example." in body
    # Nav tabs point to register in demo mode, not the protected panel routes
    assert "/institution/members" not in body
    assert "/institution/invites" not in body
    # Cross-link to the researcher demo
    assert "/demo/dashboard" in body


def test_demo_pages_in_sitemap(client):
    body = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/demo/dashboard" in body
    assert "/demo/institution" in body


def test_real_dashboard_still_requires_login(client):
    # Regression: demo_mode must not leak; the real dashboard stays gated.
    response = client.get("/dashboard", follow_redirects=False)
    assert response.status_code in (301, 302)
    assert "/auth/login" in response.headers.get("Location", "")


def test_landing_links_to_both_demos(client):
    body = client.get("/").get_data(as_text=True)
    assert "/demo/dashboard" in body
    assert "/demo/institution" in body
