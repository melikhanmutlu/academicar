"""Admin institutions pages: list/detail clean-up and the Mark paid action."""
from models import Payment, db
from tests.test_admin_phase0_fixes import _make_admin
from tests.test_institutions import create_institution


def _pending_payment(institution_id):
    payment = Payment(
        institution_id=institution_id,
        amount_kurus=250000,
        currency="TRY",
        provider="manual",
        status="pending",
    )
    db.session.add(payment)
    db.session.commit()
    return payment.id


def test_detail_marks_pending_payment_paid_and_returns_to_detail(client):
    _make_admin(client)
    with client.application.app_context():
        institution_id = create_institution(name="Pay Uni").id
        payment_id = _pending_payment(institution_id)
    page = client.get(f"/admin/institutions/{institution_id}")
    assert b"Mark paid" in page.data
    assert b"Danger zone" in page.data
    content = page.data.split(b"</header>", 1)[-1]
    assert b'style="' not in content.split(b"<main", 1)[-1]

    response = client.post(
        f"/admin/payments/{payment_id}/status",
        data={"status": "paid", "next": f"/admin/institutions/{institution_id}"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/admin/institutions/{institution_id}")
    with client.application.app_context():
        assert db.session.get(Payment, payment_id).status == "paid"
    assert b"Mark paid" not in client.get(f"/admin/institutions/{institution_id}").data


def test_list_collapses_create_form_and_flags_ended_contract(client):
    _make_admin(client)
    with client.application.app_context():
        from datetime import UTC, datetime, timedelta

        create_institution(name="Ended Uni", contract_ends_at=datetime.now(UTC) - timedelta(days=3))
    html = client.get("/admin/institutions").get_data(as_text=True)
    assert "New institution" in html
    assert 'id="create"' in html and 'id="create" open' not in html
    assert "1 institution<" in html
    assert "Expired" in html
