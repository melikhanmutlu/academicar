"""GA4 events: server actions are queued in the session and fired on the next
page by templates/_google_analytics.html, only behind cookie consent."""
from analytics import ga_content_group
from tests.test_ux_audit_fixes import _register_form


def _enable_ga(client):
    client.application.config["GA_MEASUREMENT_ID"] = "G-TEST123"


def test_no_events_queued_without_measurement_id(client):
    client.post("/auth/register", data=_register_form("noga@example.com", accept_terms="y"))
    with client.session_transaction() as sess:
        assert "ga_events" not in sess


def test_sign_up_is_queued_and_rendered_once(client):
    _enable_ga(client)
    resp = client.post("/auth/register", data=_register_form("ga@example.com", accept_terms="y"))
    assert resp.status_code in (302, 303)
    with client.session_transaction() as sess:
        assert sess["ga_events"] == [{"name": "sign_up", "params": {"method": "email"}}]
    body = client.get("/").get_data(as_text=True)
    assert '"name": "sign_up"' in body and '"method": "email"' in body
    assert "ga@example.com" not in body.split("var pending")[1].split(";")[0]
    # Shown once: the next page has nothing pending.
    assert '"name": "sign_up"' not in client.get("/").get_data(as_text=True)


def test_institutional_inquiry_queues_generate_lead(client):
    _enable_ga(client)
    resp = client.post(
        "/institutional",
        data={"institution_name": "Uni", "contact_name": "Ada", "email": "ada@uni.edu", "message": "Hi"},
        follow_redirects=True,
    )
    pending = resp.get_data(as_text=True).split("var pending = ")[1].split(";")[0]
    assert '"name": "generate_lead"' in pending and '"form": "institutional"' in pending
    assert "ada@uni.edu" not in pending


def test_tracking_helpers_and_content_group_on_page(client):
    _enable_ga(client)
    body = client.get("/blog").get_data(as_text=True)
    assert "window.aarTrack" in body
    assert 'var CONTENT_GROUP = "blog"' in body


def test_content_groups():
    assert ga_content_group("/") == "home"
    assert ga_content_group("/blog/open-step-stp-file-online") == "blog"
    assert ga_content_group("/ar-for-anatomy") == "use_case"
    assert ga_content_group("/view/abc") == "viewer"
    assert ga_content_group("/terms") == "other"
