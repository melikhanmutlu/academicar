"""Project collaborators: owner + editors, invited by email."""
import os
import uuid
from datetime import UTC, datetime

from models import Model3D, Paper, ProjectCollaborator, User, db
from tests.conftest import create_user, login, register, upload_file_bytes, valid_ascii_stl_bytes

OWNER = "owner@example.com"
EDITOR = "editor@example.com"


def _capture_mail(monkeypatch):
    sent = []
    monkeypatch.setattr("utils.email.send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def _user(email, verified=True):
    user = create_user(email=email, username=email.split("@")[0])
    if verified:
        user.email_verified_at = datetime.now(UTC)
        db.session.commit()
    return user


def _project(app, visibility="unlisted"):
    """Owner's project with one ready model; returns (slug, model_id)."""
    with app.app_context():
        owner = User.query.filter_by(email=OWNER).one()
        paper = Paper(title="Spine atlas", slug=f"spine-{uuid.uuid4().hex[:8]}", user_id=owner.id,
                      visibility=visibility, is_public=visibility == "public", share_token="tok-" + uuid.uuid4().hex)
        db.session.add(paper)
        db.session.flush()
        model = Model3D(id=str(uuid.uuid4()), paper_id=paper.id, user_id=owner.id, display_name="Vertebra",
                        glb_path="converted/x/model.glb", license_type="academic", processing_status="ready")
        folder = os.path.join(app.config["CONVERTED_FOLDER"], model.id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "model.glb"), "wb") as fh:
            fh.write(b"glTF")
        db.session.add(model)
        db.session.commit()
        return paper.slug, model.id


def _setup(app, editor_verified=True):
    with app.app_context():
        _user(OWNER)
        _user(EDITOR, verified=editor_verified)
        _user("stranger@example.com")
    return _project(app)


def _invite(client, slug, email=EDITOR):
    return client.post(f"/projects/{slug}/collaborators", data={"email": email}, follow_redirects=True)


def _switch(client, email):
    client.post("/auth/logout")
    login(client, email=email)


def test_owner_adds_a_confirmed_account_who_can_then_edit(client, app, monkeypatch):
    sent = _capture_mail(monkeypatch)
    slug, model_id = _setup(app)
    login(client, email=OWNER)
    html = _invite(client, slug, "Editor@Example.com").get_data(as_text=True)
    assert "editor@example.com can now edit this project." in html
    assert [to for to, _s, _b in sent] == [EDITOR]
    assert f"/projects/{slug}" in sent[0][2]
    with app.app_context():
        row = ProjectCollaborator.query.one()
        assert row.user_id == User.query.filter_by(email=EDITOR).one().id and row.accepted_at

    _switch(client, EDITOR)
    page = client.get(f"/projects/{slug}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "data-visibility-readonly" in html and "/visibility\" class=\"visibility-choice" not in html
    assert f"/models/{model_id}/delete" not in html
    assert "Leave project" in html and 'name="email"' not in html
    assert client.get(f"/models/{model_id}/edit").status_code == 200
    assert "Delete Model" not in client.get(f"/models/{model_id}/edit").get_data(as_text=True)
    assert "Shared with you" in client.get("/dashboard").get_data(as_text=True)


def test_editor_edits_content_but_not_owner_controls(client, app, monkeypatch):
    _capture_mail(monkeypatch)
    slug, model_id = _setup(app)
    login(client, email=OWNER)
    _invite(client, slug)
    _switch(client, EDITOR)

    edit = client.get(f"/projects/{slug}/edit").get_data(as_text=True)
    assert "Delete project" not in edit and 'name="visibility"' not in edit
    # A forged visibility field is ignored for editors.
    client.post(f"/projects/{slug}/edit", data={"title": "Spine atlas v2", "visibility": "public"}, follow_redirects=True)
    client.post(f"/models/{model_id}/edit", data={"display_name": "L4 vertebra"}, follow_redirects=True)
    with app.app_context():
        paper = Paper.query.filter_by(slug=slug).one()
        assert paper.title == "Spine atlas v2"
        assert paper.visibility == "unlisted"
        assert db.session.get(Model3D, model_id).display_name == "L4 vertebra"

    assert client.post(f"/models/{model_id}/delete").status_code == 403
    assert client.post(f"/papers/{slug}/delete").status_code == 403
    assert client.post(f"/projects/{slug}/visibility", data={"visibility": "public"}).status_code == 403
    assert client.get(f"/models/{model_id}/upgrade").status_code == 403
    assert client.post(f"/projects/{slug}/collaborators", data={"email": "x@example.com"}).status_code == 403
    with app.app_context():
        assert db.session.get(Model3D, model_id) is not None
        assert Paper.query.filter_by(slug=slug).one().status != "deleted"


def test_editor_upload_belongs_to_the_owner(client, app, monkeypatch):
    _capture_mail(monkeypatch)
    slug, _model_id = _setup(app)
    login(client, email=OWNER)
    _invite(client, slug)
    _switch(client, EDITOR)
    client.post(
        f"/papers/{slug}/upload-model",
        data={"file": upload_file_bytes(valid_ascii_stl_bytes(), "pelvis.stl"), "compliance_confirm": "yes",
              "source_unit": "mm", "display_name": "Pelvis"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    with app.app_context():
        model = Model3D.query.filter_by(display_name="Pelvis").one()
        owner = User.query.filter_by(email=OWNER).one()
        editor = User.query.filter_by(email=EDITOR).one()
        assert model.user_id == owner.id
        assert model.uploaded_by_user_id == editor.id


def test_strangers_and_removed_editors_are_locked_out(client, app, monkeypatch):
    _capture_mail(monkeypatch)
    slug, model_id = _setup(app)
    _switch(client, "stranger@example.com")
    assert client.get(f"/projects/{slug}").status_code == 403
    assert client.get(f"/models/{model_id}/edit").status_code == 403

    _switch(client, OWNER)
    _invite(client, slug)
    with app.app_context():
        row_id = ProjectCollaborator.query.one().id
    _switch(client, "stranger@example.com")
    assert client.post(f"/projects/{slug}/collaborators/{row_id}/remove").status_code == 403

    _switch(client, OWNER)
    html = client.post(f"/projects/{slug}/collaborators/{row_id}/remove", follow_redirects=True).get_data(as_text=True)
    assert "can no longer edit this project" in html
    _switch(client, EDITOR)
    assert client.get(f"/projects/{slug}").status_code == 403


def test_editor_can_leave(client, app, monkeypatch):
    _capture_mail(monkeypatch)
    slug, _model_id = _setup(app)
    login(client, email=OWNER)
    _invite(client, slug)
    with app.app_context():
        row_id = ProjectCollaborator.query.one().id
    _switch(client, EDITOR)
    html = client.post(f"/projects/{slug}/collaborators/{row_id}/remove", follow_redirects=True).get_data(as_text=True)
    assert "You left" in html
    with app.app_context():
        assert ProjectCollaborator.query.count() == 0
    assert "Shared with you" not in client.get("/dashboard").get_data(as_text=True)


def test_unconfirmed_account_waits_until_it_confirms(client, app, monkeypatch):
    sent = _capture_mail(monkeypatch)
    slug, _model_id = _setup(app, editor_verified=False)
    login(client, email=OWNER)
    html = _invite(client, slug).get_data(as_text=True)
    assert "becomes an editor after signing up" in html
    assert "/auth/register" in sent[-1][2]
    with app.app_context():
        assert ProjectCollaborator.query.one().user_id is None

    _switch(client, EDITOR)
    assert client.get(f"/projects/{slug}").status_code == 403
    from auth import generate_email_verification_token

    with app.app_context():
        token = generate_email_verification_token(User.query.filter_by(email=EDITOR).one())
    client.get(f"/auth/verify-email/{token}", follow_redirects=True)
    assert client.get(f"/projects/{slug}").status_code == 200


def test_invite_to_a_new_address_is_claimed_after_signup_and_confirmation(client, app, monkeypatch):
    sent = _capture_mail(monkeypatch)
    slug, _model_id = _setup(app)
    login(client, email=OWNER)
    _invite(client, slug, "newcomer@example.com")
    assert _invite(client, slug, "newcomer@example.com").get_data(as_text=True).count("is already invited") == 1
    owner_html = client.get(f"/projects/{slug}").get_data(as_text=True)
    assert "newcomer@example.com" in owner_html and "Invited" in owner_html

    client.post("/auth/logout")
    register(client, email="newcomer@example.com", username="Newcomer")
    assert client.get(f"/projects/{slug}").status_code == 403  # not confirmed yet
    link = next(line.strip() for line in sent[-1][2].splitlines() if "/auth/verify-email/" in line)
    client.get("/" + link.split("/", 3)[3], follow_redirects=True)
    assert client.get(f"/projects/{slug}").status_code == 200
    with app.app_context():
        row = ProjectCollaborator.query.filter_by(email="newcomer@example.com").one()
        assert row.user_id == User.query.filter_by(email="newcomer@example.com").one().id


def test_owner_cannot_invite_themselves_or_bad_addresses(client, app, monkeypatch):
    _capture_mail(monkeypatch)
    slug, _model_id = _setup(app)
    login(client, email=OWNER)
    assert "your own address" in _invite(client, slug, OWNER).get_data(as_text=True)
    assert "valid email" in _invite(client, slug, "not-an-email").get_data(as_text=True)
    with app.app_context():
        assert ProjectCollaborator.query.count() == 0


def test_deleting_the_editor_account_keeps_their_uploads_with_the_owner(client, app, monkeypatch):
    _capture_mail(monkeypatch)
    slug, model_id = _setup(app)
    login(client, email=OWNER)
    _invite(client, slug)
    with app.app_context():
        editor = User.query.filter_by(email=EDITOR).one()
        db.session.get(Model3D, model_id).uploaded_by_user_id = editor.id
        db.session.commit()
    _switch(client, EDITOR)
    client.post("/account/delete", data={"confirm": "DELETE", "current_password": "password123"})
    with app.app_context():
        assert User.query.filter_by(email=EDITOR).first() is None
        assert ProjectCollaborator.query.count() == 0
        model = db.session.get(Model3D, model_id)
        assert model is not None and model.uploaded_by_user_id is None
