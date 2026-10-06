from models import BlogPost, db
from tests.test_admin_blog import _login_admin


def _first_post_id(app):
    with app.app_context():
        return BlogPost.query.order_by(BlogPost.id).first().id


def test_validation_error_keeps_typed_content(client, app):
    _login_admin(client, app)
    resp = client.post(
        "/admin/blog/create",
        data={"title": "Typed title", "description": "Kept summary", "tags": "a, b", "body": "   "},
    )
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Title and body are required." in html
    assert 'value="Typed title"' in html
    assert "Kept summary" in html
    assert 'id="blog-new" class="admin-blog-new" open' in html
    with app.app_context():
        assert BlogPost.query.filter_by(title="Typed title").count() == 0


def test_update_validation_error_keeps_content_in_edit_mode(client, app):
    _login_admin(client, app)
    client.get("/admin/blog")
    pid = _first_post_id(app)
    resp = client.post(f"/admin/blog/{pid}/update", data={"title": "Edited but no body", "body": ""})
    html = resp.get_data(as_text=True)
    assert 'value="Edited but no body"' in html
    assert f"/admin/blog/{pid}/update" in html


def test_invalid_edit_id_shows_message(client, app):
    _login_admin(client, app)
    resp = client.get("/admin/blog?edit=99999", follow_redirects=True)
    assert "Post not found." in resp.get_data(as_text=True)


def test_new_form_collapsed_and_edit_form_open(client, app):
    _login_admin(client, app)
    html = client.get("/admin/blog").get_data(as_text=True)
    assert 'id="blog-new"' in html
    assert 'id="blog-new" class="admin-blog-new" open' not in html
    pid = _first_post_id(app)
    edit_html = client.get(f"/admin/blog?edit={pid}").get_data(as_text=True)
    assert 'id="blog-new" class="admin-blog-new" open' in edit_html
    assert "Update post" in edit_html


def test_read_minutes_default_matches_public_page(client, app):
    _login_admin(client, app)
    html = client.get("/admin/blog").get_data(as_text=True)
    assert 'name="read_minutes"' in html
    assert 'value="5"' not in html


def test_delete_confirm_names_post_and_no_inline_widths(client, app):
    _login_admin(client, app)
    html = client.get("/admin/blog").get_data(as_text=True)
    assert "permanently?" in html
    assert "max-width:520px" not in html


def test_list_does_not_load_post_bodies(client, app):
    from sqlalchemy import event

    _login_admin(client, app)
    client.get("/admin/blog")  # seed
    stmts = []
    with app.app_context():
        engine = db.engine
        listener = lambda conn, cur, stmt, *a: stmts.append(stmt)
        event.listen(engine, "before_cursor_execute", listener)
        try:
            client.get("/admin/blog")
        finally:
            event.remove(engine, "before_cursor_execute", listener)
    list_stmts = [s for s in stmts if "FROM blog_posts" in s and "ORDER BY" in s]
    assert list_stmts and all("blog_posts.body" not in s for s in list_stmts)


def test_revisit_does_not_commit_when_nothing_to_seed(client, app, monkeypatch):
    _login_admin(client, app)
    client.get("/admin/blog")
    commits = []
    orig = db.session.commit
    monkeypatch.setattr(db.session, "commit", lambda: commits.append(1) or orig())
    client.get("/admin/blog")
    assert commits == []
