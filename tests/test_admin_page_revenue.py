"""Revenue page clean-up: no duplicated overview blocks, filter links,
filter-preserving redirects, provider-neutral refund note."""
from models import Payment, db
from tests.test_admin_phase0_fixes import _make_admin


def _pay(status="pending", provider="manual", ref=None):
    p = Payment(plan_key="academic", amount_kurus=990, currency="USD", provider=provider,
                provider_reference=ref, status=status)
    db.session.add(p)
    db.session.commit()
    return p.id


def test_revenue_page_drops_overview_duplicates_and_links_states(client):
    _make_admin(client)
    with client.application.app_context():
        _pay("paid", ref="r1")
        _pay("pending", ref="r2")
    html = client.get("/admin/revenue").get_data(as_text=True)
    assert "License distribution" not in html
    assert "Licenses expiring soon" not in html
    assert "Commercial analytics" not in html
    assert "PayTR" not in html
    assert 'href="/admin/revenue?pay_status=paid"' in html
    assert "2 payments" in html


def test_revenue_empty_chart_state(client):
    _make_admin(client)
    html = client.get("/admin/revenue").get_data(as_text=True)
    assert "No paid revenue in the last 12 months." in html


def test_invalid_status_and_delete_pending_keep_filters(client):
    _make_admin(client)
    with client.application.app_context():
        pid = _pay("pending", ref="r3")
    nxt = "/admin/revenue?pay_status=pending&provider=manual"
    resp = client.post(f"/admin/payments/{pid}/status", data={"status": "bogus", "next": nxt})
    assert resp.headers["Location"].endswith(nxt)
    resp = client.post("/admin/payments/delete-pending", data={"next": nxt})
    assert resp.headers["Location"].endswith(nxt)
    with client.application.app_context():
        assert Payment.query.filter_by(status="pending").count() == 0
