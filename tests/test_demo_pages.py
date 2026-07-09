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
    # Activity feed + contributors (rich overview)
    assert "Recent activity" in body
    assert "added in the last 30 days" in body
    assert "Newest members" in body
    assert "Recently added models" in body
    assert "Top contributors" in body
    assert "Elif Demir" in body
    # Institution logo featured prominently in the panel header
    assert "images/demo-institution-logo.svg" in body
    # Nav tabs are now live demo sections, not the protected panel routes
    assert "tab=members" in body
    assert "tab=invites" in body
    assert "tab=models" in body
    assert "/institution/members" not in body
    assert "/institution/invites" not in body
    # Cross-link to the researcher demo
    assert "/demo/dashboard" in body


def test_demo_institution_overview_links_are_interactive_and_privacy_aware(client):
    body = client.get("/demo/institution").get_data(as_text=True)
    # Public funded models link out to a real viewer experience; publications
    # resolve to the sign-up CTA.
    assert "/demo/mitochondria/ar" in body
    assert "/auth/register" in body
    # A private upload is surfaced but marked private (never linked publicly).
    assert "Patient-Specific Aortic Aneurysm" in body
    assert "Private" in body


def test_demo_institution_members_tab(client):
    response = client.get("/demo/institution?tab=members")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Elif Demir" in body
    assert "elif.demir@boun.edu.tr" in body
    assert "Institution admin" in body  # admin role chip
    # Management actions become a sign-up CTA; no login-gated member routes leak.
    assert "/institution/members/" not in body
    assert "/institution/members.csv" not in body
    assert "/auth/register" in body


def test_demo_institution_invites_tab(client):
    response = client.get("/demo/institution?tab=invites")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Create invite link" in body
    assert "Invite links" in body
    # Create/revoke are login-gated in reality → demo routes them to sign-up.
    assert "/institution/invites/create" not in body
    assert "Sign up to create invites" in body


def test_demo_institution_models_tab(client):
    response = client.get("/demo/institution?tab=models")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Hippocampal Neuron Reconstruction" in body  # public model row
    assert "Cadaveric Knee Ligament Study" in body      # private model row
    # Public rows link to a viewer; private rows are labelled, not linked.
    assert "/demo/mitochondria/ar" in body
    assert "Private" in body
    # The unfiltered login-gated models route must not leak into the demo.
    assert "/institution/models" not in body


def test_demo_institution_unknown_tab_falls_back_to_overview(client):
    response = client.get("/demo/institution?tab=bogus")
    assert response.status_code == 200
    assert "Recent activity" in response.get_data(as_text=True)


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
