"""Regressions from the admin panel audit (v37)."""
from datetime import UTC, datetime

from models import Payment, db
from tests.test_admin_panel import _make_admin


def test_revenue_is_reported_per_currency(client):
    _make_admin(client)
    with client.application.app_context():
        db.session.add_all([
            Payment(amount_kurus=1990, currency="USD", provider="dev", status="paid", paid_at=datetime.now(UTC)),
            Payment(amount_kurus=2500000, currency="TRY", provider="manual", status="paid", paid_at=datetime.now(UTC)),
        ])
        db.session.commit()
    html = client.get("/admin/revenue").get_data(as_text=True)
    assert "19.90 USD" in html
    assert "25000.00 TRY" in html
    assert "25019.90" not in html


def test_month_starts_never_skip_or_repeat():
    from app import last_n_month_starts

    starts = last_n_month_starts(datetime(2026, 10, 1, 9, 0, tzinfo=UTC), 12)
    labels = [d.strftime("%Y-%m") for d in starts]
    assert labels == [
        "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
        "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10",
    ]


def test_deleted_builtin_blog_post_stays_deleted(client, app):
    from app import seed_builtin_blog_posts
    from blog_content import get_all_posts
    from models import BlogPost

    _make_admin(client)
    slug = get_all_posts()[0]["slug"]
    with app.app_context():
        seed_builtin_blog_posts(app)
        post_id = BlogPost.query.filter_by(slug=slug).one().id
    client.post(f"/admin/blog/{post_id}/delete", follow_redirects=True)
    client.get("/admin/blog")
    with app.app_context():
        seed_builtin_blog_posts(app)
        assert BlogPost.query.filter_by(slug=slug).count() == 0
    client.post("/auth/logout")
    assert client.get(f"/blog/{slug}").status_code == 404
    assert f"/blog/{slug}" not in client.get("/blog").get_data(as_text=True)


def test_unpublished_builtin_post_is_hidden_but_admin_can_preview(client, app):
    from app import seed_builtin_blog_posts
    from blog_content import get_all_posts
    from models import BlogPost

    _make_admin(client)
    slug = get_all_posts()[0]["slug"]
    with app.app_context():
        seed_builtin_blog_posts(app)
        BlogPost.query.filter_by(slug=slug).one().is_published = False
        db.session.commit()
    assert client.get(f"/blog/{slug}").status_code == 200  # admin preview
    client.post("/auth/logout")
    assert client.get(f"/blog/{slug}").status_code == 404


def test_content_csv_follows_the_visibility_filter(client, app):
    from models import Paper, User

    _make_admin(client)
    with app.app_context():
        owner = User.query.filter_by(email="admin@example.com").one()
        for vis in ("private", "unlisted", "public"):
            db.session.add(Paper(title=f"P {vis}", slug=f"p-{vis}", user_id=owner.id,
                                 visibility=vis, is_public=vis == "public"))
        db.session.commit()
    body = client.get("/admin/content/export.csv?paper_visibility=unlisted").get_data(as_text=True)
    lines = body.strip().splitlines()
    assert "visibility" in lines[0]
    assert len(lines) == 2 and "p-unlisted" in lines[1]


def test_admin_delete_then_restore_keeps_visibility(client, app):
    from models import Paper, User

    _make_admin(client)
    with app.app_context():
        owner = User.query.filter_by(email="admin@example.com").one()
        paper = Paper(title="Review", slug="review-proj", user_id=owner.id, visibility="unlisted",
                      is_public=False, share_token="tok-review-123")
        db.session.add(paper)
        db.session.commit()
        paper_id = paper.id
    client.post(f"/admin/papers/{paper_id}/visibility", data={"visibility": "unlisted", "status": "deleted"},
                follow_redirects=True)
    with app.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.status == "deleted" and paper.deleted_at is not None
    html = client.post(f"/admin/papers/{paper_id}/restore", follow_redirects=True).get_data(as_text=True)
    assert "Review link" in html
    with app.app_context():
        paper = db.session.get(Paper, paper_id)
        assert paper.status == "active" and paper.visibility == "unlisted"
