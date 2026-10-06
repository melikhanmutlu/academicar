"""Users admin page: payment return URL, query count, guarded buttons."""
from sqlalchemy import event

from models import AuditLog, Model3D, Paper, User, db


def _admin_client(app, client):
    with app.app_context():
        admin = User(email="adm@example.com", username="Adm", is_admin=True)
        admin.set_password("password123")
        member = User(email="mem@example.com", username="Mem")
        member.set_password("password123")
        db.session.add_all([admin, member])
        db.session.commit()
        admin_id, member_id = admin.id, member.id
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["_fresh"] = True
    return admin_id, member_id


def test_manual_payment_returns_to_next_page(app, client):
    _, member_id = _admin_client(app, client)
    resp = client.post(
        "/admin/payments/create",
        data={"user_email": "mem@example.com", "amount": "10", "status": "pending", "next": f"/admin/users/{member_id}"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/admin/users/{member_id}")
    resp = client.post(
        "/admin/payments/create",
        data={"user_email": "nobody@example.com", "amount": "10", "next": f"/admin/users/{member_id}"},
    )
    assert resp.headers["Location"].endswith(f"/admin/users/{member_id}")
    resp = client.post("/admin/payments/create", data={"user_email": "mem@example.com", "amount": "10", "next": "https://evil.test/"})
    assert "/admin/revenue" in resp.headers["Location"]


def test_detail_form_posts_next_and_hides_pointless_deactivate(app, client):
    admin_id, member_id = _admin_client(app, client)
    html = client.get(f"/admin/users/{member_id}").get_data(as_text=True)
    assert 'name="next" value="/admin/users/%d"' % member_id in html
    assert "Deactivate account" in html
    own = client.get(f"/admin/users/{admin_id}").get_data(as_text=True)
    assert "Deactivate account" not in own
    assert "records" not in html


def test_detail_and_dashboard_query_count_is_flat(app, client):
    _, member_id = _admin_client(app, client)
    with app.app_context():
        for i in range(12):
            paper = Paper(title=f"P{i}", slug=f"p-{i}", user_id=member_id, status="active")
            db.session.add(paper)
            db.session.flush()
            db.session.add(Model3D(id=f"m{i}", public_id=f"pub{i}", paper_id=paper.id, user_id=member_id,
                                   glb_path=f"converted/{i}/model.glb", original_filename="a.stl", processing_status="ready"))
        db.session.commit()
        engine = db.engine

    def count(url):
        n = [0]

        def hook(*_a, **_k):
            n[0] += 1

        event.listen(engine, "before_cursor_execute", hook)
        try:
            assert client.get(url).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", hook)
        return n[0]

    assert count(f"/admin/users/{member_id}") < 30
    assert count(f"/admin/users/{member_id}/dashboard") < 30
    with app.app_context():
        assert AuditLog.query.filter_by(event_type="admin_user_detail_viewed").count() == 1
        assert AuditLog.query.filter_by(event_type="admin_user_dashboard_viewed").count() == 1


def test_list_counter_and_csv_link(app, client):
    _admin_client(app, client)
    html = client.get("/admin/users?user_q=mem&page=1").get_data(as_text=True)
    assert "1 user<" in html
    csv_link = html.split("Download CSV")[0].rsplit("<a", 1)[-1]
    assert "page=1" not in csv_link and "user_q=mem" in csv_link
