"""Institutional (B2B) feature tests: quota-funded licensing, contract
lifecycle, invites/panel, and platform-admin CRUD."""

from datetime import UTC, datetime, timedelta

from institutions import (
    end_institution_access_now,
    institution_usage,
    renew_institution_contract,
)
from licensing import MB, apply_model_license_defaults, model_access_status
from models import Institution, InstitutionInvite, InstitutionMember, Model3D, Paper, db


def create_institution(
    name="Test University",
    status="active",
    contract_ends_at="default",
    quota_model_count=None,
    quota_storage_bytes=None,
    email_domains=None,
    **kw,
):
    if contract_ends_at == "default":
        contract_ends_at = datetime.now(UTC) + timedelta(days=365)
    institution = Institution(
        name=name,
        status=status,
        contract_starts_at=kw.pop("contract_starts_at", datetime.now(UTC) - timedelta(days=1)),
        contract_ends_at=contract_ends_at,
        quota_model_count=quota_model_count,
        quota_storage_bytes=quota_storage_bytes,
        email_domains=email_domains,
        **kw,
    )
    db.session.add(institution)
    db.session.commit()
    return institution


def add_member(institution, user, role="member"):
    member = InstitutionMember(institution_id=institution.id, user_id=user.id, role=role)
    db.session.add(member)
    db.session.commit()
    return member


def make_invite(institution, **kw):
    import secrets

    invite = InstitutionInvite(
        institution_id=institution.id,
        token=kw.pop("token", secrets.token_urlsafe(24)),
        **kw,
    )
    db.session.add(invite)
    db.session.commit()
    return invite


def upload_model_for(client, title="Inst Paper", filename="model.stl", visibility=None):
    """Create a paper and upload a valid STL to it; returns the Model3D id.
    visibility: None uses the form default (public), or "private"/"public"."""
    from tests.conftest import upload_file_bytes, valid_ascii_stl_bytes

    paper_data = {"title": title}
    if visibility is not None:
        paper_data["visibility"] = visibility
    client.post("/papers/new", data=paper_data, follow_redirects=True)
    with client.application.app_context():
        slug = Paper.query.filter_by(title=title).one().slug
    response = client.post(
        f"/papers/{slug}/upload-model",
        data={
            "file": upload_file_bytes(valid_ascii_stl_bytes(), filename),
            "compliance_confirm": "yes",
            "source_unit": "cm",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        paper = Paper.query.filter_by(title=title).one()
        model = Model3D.query.filter_by(paper_id=paper.id).one()
        return model.id


# --- Institution model helpers -------------------------------------------------


def test_domain_list_and_matching(app):
    with app.app_context():
        institution = create_institution(email_domains=" @Boun.edu.tr , metu.edu.tr ,")
        assert institution.domain_list() == ["boun.edu.tr", "metu.edu.tr"]
        assert institution.email_matches_domains("ali@boun.edu.tr")
        assert institution.email_matches_domains("ali@BOUN.EDU.TR")
        assert not institution.email_matches_domains("ali@gmail.com")
        assert not institution.email_matches_domains("")

        open_institution = create_institution(name="Open Uni", email_domains=None)
        assert open_institution.email_matches_domains("anyone@anywhere.com")


def test_contract_is_current_windows(app):
    with app.app_context():
        now = datetime.now(UTC)
        current = create_institution(name="A", contract_ends_at=now + timedelta(days=10))
        assert current.contract_is_current()

        expired = create_institution(name="B", contract_ends_at=now - timedelta(days=1))
        assert not expired.contract_is_current()

        future = create_institution(
            name="C",
            contract_starts_at=now + timedelta(days=5),
            contract_ends_at=now + timedelta(days=30),
        )
        assert not future.contract_is_current()

        open_ended = create_institution(name="D", contract_ends_at=None)
        assert open_ended.contract_is_current()


# --- Upload licensing ----------------------------------------------------------


def test_member_upload_gets_institutional_license(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution(quota_model_count=10)
        add_member(institution, user)
        contract_end = institution.contract_ends_at
        institution_id = institution.id

    model_id = upload_model_for(client)

    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.license_type == "institutional"
        assert model.institution_id == institution_id
        assert model.access_expires_at == contract_end.replace(tzinfo=None)
        assert model.storage_limit_bytes == 500 * MB
        assert model.license_status == "active"


def test_quota_model_count_boundary(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution(quota_model_count=1)
        add_member(institution, user)

    first_id = upload_model_for(client, title="First Paper")
    second_id = upload_model_for(client, title="Second Paper")

    with client.application.app_context():
        first = db.session.get(Model3D, first_id)
        second = db.session.get(Model3D, second_id)
        assert first.license_type == "institutional"
        assert second.license_type == "free"
        assert second.institution_id is None
        assert second.access_expires_at is not None  # free 3-day window


def test_quota_storage_boundary(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        from models import User

        user = User.query.one()
        # Far smaller than any real file: first upload already exceeds it.
        institution = create_institution(quota_storage_bytes=10)
        add_member(institution, user)

    tiny_quota_model = upload_model_for(client, title="Tiny Quota Paper")
    with client.application.app_context():
        model = db.session.get(Model3D, tiny_quota_model)
        assert model.license_type == "free"
        assert model.institution_id is None

        # Generous quota: next upload fits and is funded.
        institution = Institution.query.one()
        institution.quota_storage_bytes = 5 * MB
        db.session.commit()

    funded_model = upload_model_for(client, title="Funded Paper")
    with client.application.app_context():
        model = db.session.get(Model3D, funded_model)
        assert model.license_type == "institutional"


def test_expired_contract_and_suspension_fall_back_to_free(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution(
            contract_ends_at=datetime.now(UTC) - timedelta(days=1)
        )
        add_member(institution, user)

    expired_model = upload_model_for(client, title="Expired Contract Paper")
    with client.application.app_context():
        model = db.session.get(Model3D, expired_model)
        assert model.license_type == "free"

        institution = Institution.query.one()
        institution.contract_ends_at = datetime.now(UTC) + timedelta(days=30)
        institution.status = "suspended"
        db.session.commit()

    suspended_model = upload_model_for(client, title="Suspended Paper")
    with client.application.app_context():
        model = db.session.get(Model3D, suspended_model)
        assert model.license_type == "free"


def test_non_member_upload_unaffected(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        create_institution()  # exists, but the user is not a member

    model_id = upload_model_for(client)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.license_type == "free"
        assert model.institution_id is None


# --- Contract lifecycle over funded models -------------------------------------


def test_expired_institutional_model_shows_unavailable(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution()
        add_member(institution, user)

    model_id = upload_model_for(client)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        model.access_expires_at = datetime.now(UTC) - timedelta(hours=1)
        db.session.commit()

    response = client.get(f"/view/{model_id}")
    assert response.status_code == 410


def test_renew_contract_bulk_updates_only_owned_institutional_models(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution(quota_model_count=10)
        add_member(institution, user)

    funded_id = upload_model_for(client, title="Funded Paper")
    relicensed_id = upload_model_for(client, title="Relicensed Paper")

    with client.application.app_context():
        # An admin moved this model onto a personal paid plan; the contract
        # must no longer manage its expiry.
        relicensed = db.session.get(Model3D, relicensed_id)
        apply_model_license_defaults(relicensed, "academic")
        academic_expiry = relicensed.access_expires_at.replace(tzinfo=None)

        institution = Institution.query.filter_by(name="Test University").one()
        new_end = datetime.now(UTC) + timedelta(days=730)
        updated = renew_institution_contract(institution, new_end)
        db.session.commit()

        assert updated == 1
        funded = db.session.get(Model3D, funded_id)
        assert funded.access_expires_at == new_end.replace(tzinfo=None)
        relicensed = db.session.get(Model3D, relicensed_id)
        assert relicensed.access_expires_at == academic_expiry


def test_end_institution_access_now_expires_funded_models(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution()
        add_member(institution, user)

    model_id = upload_model_for(client)
    with client.application.app_context():
        institution = Institution.query.one()
        updated = end_institution_access_now(institution, datetime.now(UTC))
        db.session.commit()
        assert updated == 1
        model = db.session.get(Model3D, model_id)
        assert model_access_status(model) == "expired"


def test_member_leaving_keeps_model_funded(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution()
        member = add_member(institution, user)

    model_id = upload_model_for(client)
    with client.application.app_context():
        member = InstitutionMember.query.one()
        db.session.delete(member)
        db.session.commit()

        model = db.session.get(Model3D, model_id)
        institution = Institution.query.one()
        assert model.license_type == "institutional"
        assert model.institution_id == institution.id
        count, used = institution_usage(institution.id)
        assert count == 1
        assert used > 0


# --- Platform admin CRUD --------------------------------------------------------


def make_admin(client, email="admin@example.com"):
    from tests.conftest import register

    register(client, email=email, username="Admin")
    with client.application.app_context():
        from models import User

        user = User.query.filter_by(email=email).one()
        user.is_admin = True
        db.session.commit()
        return user.id


def test_admin_institutions_page_requires_admin(client):
    from tests.conftest import register

    register(client)
    assert client.get("/admin/institutions").status_code == 403

    client.post("/auth/logout")
    make_admin(client)
    response = client.get("/admin/institutions")
    assert response.status_code == 200
    assert b"Create institution" in response.data


def test_admin_institution_create_and_duplicate(client):
    make_admin(client)
    response = client.post(
        "/admin/institutions/create",
        data={
            "name": "Bogazici University",
            "email_domains": " @Boun.edu.tr, boun.edu.tr , metu.edu.tr ",
            "contract_starts_at": "2026-01-01",
            "contract_ends_at": "2027-01-01",
            "annual_price": "25000",
            "currency": "try",
            "quota_model_count": "100",
            "quota_storage_mb": "1024",
            "notes": "Pilot contract",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        institution = Institution.query.one()
        assert institution.email_domains == "boun.edu.tr, metu.edu.tr"  # normalized + deduped
        assert institution.annual_price_cents == 2500000
        assert institution.currency == "TRY"
        assert institution.quota_storage_bytes == 1024 * 1024 * 1024
        from models import AuditLog

        assert AuditLog.query.filter_by(event_type="institution_created").count() == 1

    duplicate = client.post(
        "/admin/institutions/create",
        data={"name": "bogazici university"},
        follow_redirects=True,
    )
    assert b"already exists" in duplicate.data
    with client.application.app_context():
        assert Institution.query.count() == 1


def test_admin_contract_renewal_bulk_updates_models(client):
    from tests.conftest import register

    register(client, email="member@example.com", username="Member")
    with client.application.app_context():
        from models import User

        user = User.query.filter_by(email="member@example.com").one()
        institution = create_institution()
        add_member(institution, user)
        institution_id = institution.id

    model_id = upload_model_for(client)
    client.post("/auth/logout")
    make_admin(client)

    response = client.post(
        f"/admin/institutions/{institution_id}/update",
        data={
            "name": "Test University",
            "contract_ends_at": "2030-06-30",
            "currency": "TRY",
        },
        follow_redirects=True,
    )
    assert b"refreshed on 1 model(s)" in response.data
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.access_expires_at == datetime(2030, 6, 30)


def test_admin_end_access_and_suspend(client):
    from tests.conftest import register

    register(client, email="member@example.com", username="Member")
    with client.application.app_context():
        from models import User

        user = User.query.filter_by(email="member@example.com").one()
        institution = create_institution()
        add_member(institution, user)
        institution_id = institution.id

    model_id = upload_model_for(client)
    client.post("/auth/logout")
    make_admin(client)

    client.post(f"/admin/institutions/{institution_id}/status", data={"status": "suspended"}, follow_redirects=True)
    with client.application.app_context():
        institution = db.session.get(Institution, institution_id)
        assert institution.status == "suspended"
        # Suspension alone does not expire existing access.
        model = db.session.get(Model3D, model_id)
        assert model_access_status(model) == "active"

    client.post(f"/admin/institutions/{institution_id}/end-access", follow_redirects=True)
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        assert model_access_status(model) == "expired"


def test_admin_assign_institution_admin_by_email(client):
    from tests.conftest import create_user

    make_admin(client)
    with client.application.app_context():
        create_user(email="prof@boun.edu.tr", username="Prof")
        institution = create_institution()
        other = create_institution(name="Other Uni")
        other_user = create_user(email="taken@example.com", username="Taken")
        add_member(other, other_user)
        institution_id = institution.id

    unknown = client.post(
        f"/admin/institutions/{institution_id}/admins",
        data={"email": "ghost@example.com"},
        follow_redirects=True,
    )
    assert b"No account found" in unknown.data

    taken = client.post(
        f"/admin/institutions/{institution_id}/admins",
        data={"email": "taken@example.com"},
        follow_redirects=True,
    )
    assert b"another institution" in taken.data

    ok = client.post(
        f"/admin/institutions/{institution_id}/admins",
        data={"email": "Prof@BOUN.edu.tr"},
        follow_redirects=True,
    )
    assert b"is now an institution admin" in ok.data
    with client.application.app_context():
        member = InstitutionMember.query.filter_by(institution_id=institution_id).one()
        assert member.role == "admin"


def test_admin_institution_payment_has_no_license_side_effects(client):
    from tests.conftest import create_user

    make_admin(client)
    with client.application.app_context():
        institution = create_institution()
        institution_id = institution.id

    response = client.post(
        f"/admin/institutions/{institution_id}/payments",
        data={"amount": "25000", "currency": "TRY", "status": "paid", "reference": "PO-42"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with client.application.app_context():
        from models import Payment

        payment = Payment.query.one()
        assert payment.institution_id == institution_id
        assert payment.plan_key == "institutional"
        assert payment.status == "paid"
        assert payment.invoice_number
        assert payment.model_id is None

    revenue = client.get("/admin/revenue")
    assert b"Test University" in revenue.data

    # Flipping status must remain side-effect free for institution payments.
    with client.application.app_context():
        from models import Payment

        payment_id = Payment.query.one().id
    client.post(f"/admin/payments/{payment_id}/status", data={"status": "refunded"}, follow_redirects=True)
    with client.application.app_context():
        from models import Payment

        assert Payment.query.one().status == "refunded"
        assert Model3D.query.count() == 0  # nothing to touch, nothing crashed


# --- Invite join flow -----------------------------------------------------------


def test_join_flow_logged_in_user(client):
    from tests.conftest import register

    register(client, email="joiner@boun.edu.tr", username="Joiner")
    with client.application.app_context():
        institution = create_institution(email_domains="boun.edu.tr")
        invite = make_invite(institution)
        token = invite.token

    page = client.get(f"/institution/join/{token}")
    assert page.status_code == 200
    assert b"Join Test University" in page.data

    response = client.post(f"/institution/join/{token}", follow_redirects=True)
    assert b"you joined Test University" in response.data
    with client.application.app_context():
        member = InstitutionMember.query.one()
        assert member.role == "member"
        assert member.invite_id is not None
        invite = InstitutionInvite.query.one()
        assert invite.use_count == 1
        from models import AuditLog

        assert AuditLog.query.filter_by(event_type="institution_member_joined").count() == 1

    # Joining again is friendly, not a crash or a duplicate row.
    again = client.post(f"/institution/join/{token}", follow_redirects=True)
    assert b"already a member" in again.data
    with client.application.app_context():
        assert InstitutionMember.query.count() == 1


def test_join_anonymous_shows_auth_ctas_and_register_next_returns(client):
    with client.application.app_context():
        institution = create_institution()
        token = make_invite(institution).token

    page = client.get(f"/institution/join/{token}")
    assert page.status_code == 200
    assert f"/auth/register?next=%2Finstitution%2Fjoin%2F{token}".encode() in page.data or \
        f"next=/institution/join/{token}".encode() in page.data

    response = client.post(
        f"/auth/register?next=/institution/join/{token}",
        data={
            "username": "New User",
            "email": "new@example.com",
            "password": "password123",
            "confirm": "password123",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/institution/join/{token}")


def test_join_domain_restriction(client):
    from tests.conftest import register

    register(client, email="outsider@gmail.com", username="Outsider")
    with client.application.app_context():
        institution = create_institution(email_domains="boun.edu.tr")
        token = make_invite(institution).token

    response = client.post(f"/institution/join/{token}")
    assert b"requires an email address at: @boun.edu.tr" in response.data
    with client.application.app_context():
        assert InstitutionMember.query.count() == 0


def test_join_one_institution_per_user(client):
    from tests.conftest import register

    register(client, email="member@example.com", username="Member")
    with client.application.app_context():
        from models import User

        user = User.query.one()
        first = create_institution(name="First Uni")
        add_member(first, user)
        second = create_institution(name="Second Uni")
        token = make_invite(second).token

    response = client.post(f"/institution/join/{token}", follow_redirects=True)
    assert b"already belong to an institution" in response.data
    with client.application.app_context():
        member = InstitutionMember.query.one()
        assert member.institution.name == "First Uni"


def test_invite_invalidity_is_generic_404(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        institution = create_institution()
        expired = make_invite(institution, expires_at=datetime.now(UTC) - timedelta(days=1))
        exhausted = make_invite(institution, max_uses=1, use_count=1)
        revoked = make_invite(institution, revoked_at=datetime.now(UTC))
        suspended_inst = create_institution(name="Suspended Uni", status="suspended")
        suspended = make_invite(suspended_inst)
        tokens = [expired.token, exhausted.token, revoked.token, suspended.token, "not-a-real-token"]

    for token in tokens:
        response = client.get(f"/institution/join/{token}")
        assert response.status_code == 404
        # One generic message for every failure mode, and no institution leak.
        assert b"invalid or has expired" in response.data
        assert b"Test University" not in response.data
        assert b"Suspended Uni" not in response.data


def test_invite_create_and_revoke_from_panel(client):
    from tests.conftest import register

    register(client, email="dean@boun.edu.tr", username="Dean")
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution()
        add_member(institution, user, role="admin")

    response = client.post(
        "/institution/invites/create",
        data={"expires_days": "14", "max_uses": "5"},
        follow_redirects=True,
    )
    assert b"Invite link created" in response.data
    with client.application.app_context():
        invite = InstitutionInvite.query.one()
        assert invite.max_uses == 5
        assert invite.expires_at is not None
        invite_id = invite.id
        token = invite.token

    invites_page = client.get("/institution/invites")
    assert token.encode() in invites_page.data

    client.post(f"/institution/invites/{invite_id}/revoke", follow_redirects=True)
    with client.application.app_context():
        assert InstitutionInvite.query.one().revoked_at is not None

    # Revoked invite no longer joins anyone.
    assert client.get(f"/institution/join/{token}").status_code == 404


# --- Panel authorization ----------------------------------------------------------


def test_panel_requires_institution_admin_role(client):
    from tests.conftest import register

    register(client, email="member@boun.edu.tr", username="Member")
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution()
        add_member(institution, user, role="member")

    for path in ("/institution/", "/institution/members", "/institution/invites", "/institution/models"):
        assert client.get(path).status_code == 403, path

    client.post("/auth/logout")
    from tests.conftest import register as register2

    register2(client, email="outsider@example.com", username="Outsider")
    assert client.get("/institution/").status_code == 403


def test_platform_admin_without_membership_gets_403_on_panel(client):
    make_admin(client)
    assert client.get("/institution/").status_code == 403


def test_panel_cross_tenant_isolation_and_self_guards(client):
    from tests.conftest import create_user, register

    register(client, email="admin-a@example.com", username="AdminA")
    with client.application.app_context():
        from models import User

        admin_a = User.query.filter_by(email="admin-a@example.com").one()
        inst_a = create_institution(name="Uni A")
        membership_a = add_member(inst_a, admin_a, role="admin")
        membership_a_id = membership_a.id

        inst_b = create_institution(name="Uni B")
        user_b = create_user(email="member-b@example.com", username="MemberB")
        member_b = add_member(inst_b, user_b)
        member_b_id = member_b.id

    # IDOR: A's admin cannot touch B's member.
    assert client.post(f"/institution/members/{member_b_id}/remove").status_code == 404
    assert client.post(f"/institution/members/{member_b_id}/role", data={"role": "admin"}).status_code == 404

    # Self guards.
    self_remove = client.post(f"/institution/members/{membership_a_id}/remove", follow_redirects=True)
    assert b"cannot remove yourself" in self_remove.data
    self_demote = client.post(
        f"/institution/members/{membership_a_id}/role", data={"role": "member"}, follow_redirects=True
    )
    assert b"cannot remove your own admin role" in self_demote.data
    with client.application.app_context():
        assert InstitutionMember.query.filter_by(id=membership_a_id).one().role == "admin"


def test_panel_member_management_and_models_list(client):
    from tests.conftest import create_user, register

    register(client, email="dean@example.com", username="Dean")
    with client.application.app_context():
        from models import User

        dean = User.query.filter_by(email="dean@example.com").one()
        institution = create_institution()
        add_member(institution, dean, role="admin")
        colleague = create_user(email="colleague@example.com", username="Colleague")
        member_row = add_member(institution, colleague)
        member_id = member_row.id

    promote = client.post(f"/institution/members/{member_id}/role", data={"role": "admin"}, follow_redirects=True)
    assert b"role updated" in promote.data
    with client.application.app_context():
        assert InstitutionMember.query.filter_by(id=member_id).one().role == "admin"

    remove = client.post(f"/institution/members/{member_id}/remove", follow_redirects=True)
    assert b"Member removed" in remove.data
    with client.application.app_context():
        assert InstitutionMember.query.filter_by(id=member_id).count() == 0

    # Panel models list shows institution-funded models (upload as the dean).
    model_id = upload_model_for(client)
    models_page = client.get("/institution/models")
    assert models_page.status_code == 200
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        name = (model.display_name or model.original_filename or model.id).encode()
    assert name in models_page.data
    assert f"/view/{model_id}".encode() in models_page.data


# --- Lead capture & member UX ----------------------------------------------------


def test_institutional_inquiry_sends_email_and_audits(client, monkeypatch):
    sent = []

    def fake_send_email(to_address, subject, body):
        sent.append((to_address, subject, body))
        return True

    monkeypatch.setattr("utils.email.send_email", fake_send_email)

    page = client.get("/institutional")
    assert page.status_code == 200
    assert b"Request institutional licensing" in page.data

    response = client.post(
        "/institutional",
        data={
            "institution_name": "Bogazici University",
            "contact_name": "Prof. Ayse",
            "email": "ayse@boun.edu.tr",
            "estimated_members": "25",
            "message": "Neuroscience lab, ~40 models/year.",
        },
        follow_redirects=True,
    )
    assert b"we received your inquiry" in response.data
    assert len(sent) == 1
    assert "Bogazici University" in sent[0][2]
    assert "ayse@boun.edu.tr" in sent[0][2]
    with client.application.app_context():
        from models import AuditLog

        row = AuditLog.query.filter_by(event_type="institution_inquiry_submitted").one()
        assert row.details["institution_name"] == "Bogazici University"


def test_institutional_inquiry_validates_required_fields(client, monkeypatch):
    sent = []
    monkeypatch.setattr("utils.email.send_email", lambda *a: sent.append(a) or True)

    missing = client.post(
        "/institutional",
        data={"institution_name": "", "contact_name": "X", "email": "x@y.edu"},
        follow_redirects=True,
    )
    assert b"Please fill in" in missing.data

    bad_email = client.post(
        "/institutional",
        data={"institution_name": "Uni", "contact_name": "X", "email": "not-an-email"},
        follow_redirects=True,
    )
    assert b"valid work email" in bad_email.data
    assert sent == []


def test_pricing_links_to_inquiry_instead_of_mailto(client):
    response = client.get("/pricing")
    assert response.status_code == 200
    assert b'href="/institutional"' in response.data
    assert b"mailto:hello@academicar.com?subject=AcademicAR%20Institutional" not in response.data


def test_dashboard_shows_institution_badge(client):
    from tests.conftest import register

    register(client, email="dean@boun.edu.tr", username="Dean")

    no_badge = client.get("/dashboard")
    assert b"Institutional access" not in no_badge.data

    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution(contract_ends_at=datetime(2027, 1, 15))
        add_member(institution, user, role="admin")

    badge = client.get("/dashboard")
    assert b"Test University" in badge.data
    assert b"Institutional access until 2027-01-15" in badge.data
    assert b"Manage institution" in badge.data


# --- Showcase, attribution, monthly reports --------------------------------------


def test_admin_create_generates_unique_slug(client):
    make_admin(client)
    client.post("/admin/institutions/create", data={"name": "Ege University"}, follow_redirects=True)
    client.post("/admin/institutions/create", data={"name": "Ege  University!"}, follow_redirects=True)
    with client.application.app_context():
        slugs = sorted(i.slug for i in Institution.query.all())
        assert slugs[0] == "ege-university"
        assert slugs[1].startswith("ege-university-")


def test_showcase_lists_only_public_funded_papers(client):
    from tests.conftest import register

    register(client, email="member@example.com", username="Member")
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution(slug="test-university")
        add_member(institution, user)

    upload_model_for(client, title="Public Funded Paper", visibility="public")
    upload_model_for(client, title="Private Funded Paper", visibility="private")

    page = client.get("/i/test-university")
    assert page.status_code == 200
    assert b"Public Funded Paper" in page.data
    assert b"Private Funded Paper" not in page.data
    assert b"Test University" in page.data

    assert client.get("/i/no-such-institution").status_code == 404


def test_viewer_shows_supported_by_attribution(client):
    from tests.conftest import register

    register(client, email="member@example.com", username="Member")
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution(slug="test-university")
        add_member(institution, user)

    funded_id = upload_model_for(client, title="Funded Paper")
    viewer = client.get(f"/view/{funded_id}")
    assert b"Supported by" in viewer.data
    assert b"Test University" in viewer.data
    assert b"/i/test-university" in viewer.data


def test_viewer_has_no_attribution_for_free_models(client):
    from tests.conftest import register

    register(client)
    model_id = upload_model_for(client)
    viewer = client.get(f"/view/{model_id}")
    assert b"Supported by" not in viewer.data


def test_sitemap_includes_showcase_with_public_funded_paper(client):
    from tests.conftest import register

    register(client, email="member@example.com", username="Member")
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution(slug="test-university")
        add_member(institution, user)

    upload_model_for(client, title="Public Funded Paper")
    with client.application.app_context():
        paper = Paper.query.one()
        paper.is_public = True
        db.session.commit()

    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert b"/i/test-university" in sitemap.data


def test_monthly_usage_report_sends_once_per_month(client, monkeypatch):
    from institutions import send_monthly_institution_reports

    sent = []
    monkeypatch.setattr(
        "utils.email.send_email", lambda to, subject, body: sent.append((to, subject, body)) or True
    )

    from tests.conftest import register

    register(client, email="dean@boun.edu.tr", username="Dean")
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution(quota_model_count=100)
        institution.created_at = datetime.now(UTC) - timedelta(days=40)
        add_member(institution, user, role="admin")
        db.session.commit()

    model_id = upload_model_for(client)

    with client.application.app_context():
        sent.clear()  # drop the registration welcome email; assert only on the report
        count = send_monthly_institution_reports()
        assert count == 1
        assert len(sent) == 1
        to, subject, body = sent[0]
        assert to == "dean@boun.edu.tr"
        assert "Test University" in subject
        assert "Institution-funded models: 1 / 100" in body
        assert "Members: 1" in body

        institution = Institution.query.one()
        assert institution.last_usage_report_at is not None
        from models import AuditLog

        assert AuditLog.query.filter_by(event_type="institution_usage_report_sent").count() == 1

        # Same month: nothing more goes out.
        assert send_monthly_institution_reports() == 0
        assert len(sent) == 1


def test_monthly_usage_report_skips_new_suspended_and_adminless(client, monkeypatch):
    from institutions import send_monthly_institution_reports

    sent = []
    monkeypatch.setattr(
        "utils.email.send_email", lambda to, subject, body: sent.append(to) or True
    )

    with client.application.app_context():
        from models import User
        from tests.conftest import create_user

        # Created this month: not due yet.
        create_institution(name="Fresh Uni")

        # Suspended: skipped even though old.
        suspended = create_institution(name="Suspended Uni", status="suspended")
        suspended.created_at = datetime.now(UTC) - timedelta(days=40)

        # Old and active but has only a plain member (no admin): composed for
        # nobody — stamped, no email.
        adminless = create_institution(name="Adminless Uni")
        adminless.created_at = datetime.now(UTC) - timedelta(days=40)
        plain_user = create_user(email="plain@example.com", username="Plain")
        add_member(adminless, plain_user, role="member")
        db.session.commit()

        count = send_monthly_institution_reports()
        assert count == 0
        assert sent == []
        assert Institution.query.filter_by(name="Adminless Uni").one().last_usage_report_at is not None
        assert Institution.query.filter_by(name="Suspended Uni").one().last_usage_report_at is None


# --- Faz 7: discoverability, showcase branding, ops automation -------------------


def test_admin_update_self_heals_missing_slug_and_shows_showcase_link(client):
    make_admin(client)
    with client.application.app_context():
        institution = create_institution(name="Legacy Uni", slug=None)
        institution_id = institution.id

    client.post(
        f"/admin/institutions/{institution_id}/update",
        data={"name": "Legacy Uni", "currency": "TRY"},
        follow_redirects=True,
    )
    with client.application.app_context():
        institution = db.session.get(Institution, institution_id)
        assert institution.slug == "legacy-uni"

    listing = client.get("/admin/institutions")
    assert b"/i/legacy-uni" in listing.data
    detail = client.get(f"/admin/institutions/{institution_id}")
    assert b"View showcase" in detail.data


def test_admin_detail_lists_funded_models(client):
    from tests.conftest import register

    register(client, email="member@example.com", username="Member")
    with client.application.app_context():
        from models import User

        user = User.query.filter_by(email="member@example.com").one()
        institution = create_institution()
        add_member(institution, user)
        institution_id = institution.id

    model_id = upload_model_for(client, title="Funded Paper")
    client.post("/auth/logout")
    make_admin(client)

    detail = client.get(f"/admin/institutions/{institution_id}")
    assert b"Funded models" in detail.data
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        name = (model.display_name or model.original_filename or model.id).encode()
    assert name in detail.data


def test_admin_logo_upload_validation_and_removal(client):
    import io

    make_admin(client)
    with client.application.app_context():
        institution = create_institution()
        institution_id = institution.id

    # Minimal valid PNG header bytes are enough for storage-level testing.
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    bad = client.post(
        f"/admin/institutions/{institution_id}/logo",
        data={"logo": (io.BytesIO(b"not an image"), "logo.txt")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Unsupported logo type" in bad.data

    ok = client.post(
        f"/admin/institutions/{institution_id}/logo",
        data={"logo": (io.BytesIO(png_bytes), "brand.png")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Logo updated" in ok.data
    with client.application.app_context():
        import os

        institution = db.session.get(Institution, institution_id)
        assert institution.logo_path
        stored = os.path.join(client.application.config["INSTITUTION_LOGO_FOLDER"], institution.logo_path)
        assert os.path.exists(stored)
        logo_filename = institution.logo_path

    assert client.get(f"/institution-logos/{logo_filename}").status_code == 200

    removed = client.post(
        f"/admin/institutions/{institution_id}/logo",
        data={"remove": "1"},
        follow_redirects=True,
    )
    assert b"Logo removed" in removed.data
    with client.application.app_context():
        assert db.session.get(Institution, institution_id).logo_path is None


def test_showcase_renders_description_logo_and_poster(client):
    from tests.conftest import register

    register(client, email="member@example.com", username="Member")
    with client.application.app_context():
        institution = create_institution(
            slug="test-university",
            public_description="Leading neuroscience 3D archive.",
            logo_path="brand.png",
        )
        from models import User

        add_member(institution, User.query.one())

    model_id = upload_model_for(client, title="Poster Paper", visibility="public")
    with client.application.app_context():
        model = db.session.get(Model3D, model_id)
        model.poster_path = "/some/where/poster.png"
        db.session.commit()

    page = client.get("/i/test-university")
    assert b"Leading neuroscience 3D archive." in page.data
    assert b"/institution-logos/brand.png" in page.data
    assert f"/files/{model_id}/poster.png".encode() in page.data
    assert b"CollectionPage" in page.data  # JSON-LD


def test_panel_showcase_settings_updates_description(client):
    from tests.conftest import register

    register(client, email="dean@example.com", username="Dean")
    with client.application.app_context():
        from models import User

        institution = create_institution()
        add_member(institution, User.query.one(), role="admin")

    overview = client.get("/institution/")
    assert b"Public showcase" in overview.data

    response = client.post(
        "/institution/showcase-settings",
        data={"public_description": "  Our lab publishes interactive anatomy.  "},
        follow_redirects=True,
    )
    assert b"Showcase description updated" in response.data
    with client.application.app_context():
        assert Institution.query.one().public_description == "Our lab publishes interactive anatomy."


def test_panel_members_csv_authz_and_content(client):
    from tests.conftest import create_user, register

    register(client, email="dean@example.com", username="Dean")
    with client.application.app_context():
        from models import User

        institution = create_institution()
        add_member(institution, User.query.filter_by(email="dean@example.com").one(), role="admin")
        colleague = create_user(email="colleague@example.com", username="Colleague")
        add_member(institution, colleague)

    csv_response = client.get("/institution/members.csv")
    assert csv_response.status_code == 200
    body = csv_response.data.decode()
    assert "dean@example.com" in body and "colleague@example.com" in body
    assert "role" in body.splitlines()[0]

    client.post("/auth/logout")
    from tests.conftest import login

    login(client, email="colleague@example.com")
    assert client.get("/institution/members.csv").status_code == 403


def test_invite_create_sends_email_when_requested(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "utils.email.send_email", lambda to, subject, body: sent.append((to, subject, body)) or True
    )

    from tests.conftest import register

    register(client, email="dean@boun.edu.tr", username="Dean")
    with client.application.app_context():
        from models import User

        institution = create_institution(email_domains="boun.edu.tr")
        add_member(institution, User.query.one(), role="admin")

    sent.clear()  # drop the registration welcome email; assert only on the invite
    response = client.post(
        "/institution/invites/create",
        data={"expires_days": "14", "send_to": "colleague@boun.edu.tr"},
        follow_redirects=True,
    )
    assert b"Invite created and emailed" in response.data
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "colleague@boun.edu.tr"
    with client.application.app_context():
        token = InstitutionInvite.query.one().token
    assert token in body
    assert "@boun.edu.tr" in body  # domain requirement note


def test_renewal_reminder_once_per_contract_end(client, monkeypatch):
    from institutions import send_contract_renewal_reminders

    sent = []
    monkeypatch.setattr(
        "utils.email.send_email", lambda to, subject, body: sent.append(to) or True
    )

    from tests.conftest import register

    register(client, email="dean@example.com", username="Dean")
    with client.application.app_context():
        from models import User

        near = create_institution(contract_ends_at=datetime.now(UTC) + timedelta(days=10))
        add_member(near, User.query.one(), role="admin")
        # Not due: far-future, open-ended, and suspended institutions.
        create_institution(name="Far Uni", contract_ends_at=datetime.now(UTC) + timedelta(days=90))
        create_institution(name="Open Uni", contract_ends_at=None)
        suspended = create_institution(
            name="Susp Uni",
            status="suspended",
            contract_ends_at=datetime.now(UTC) + timedelta(days=5),
        )

        count = send_contract_renewal_reminders()
        assert count == 1
        assert "dean@example.com" in sent
        from models import AuditLog

        assert AuditLog.query.filter_by(event_type="institution_renewal_reminder_sent").count() == 1

        # Same contract end: no repeat.
        assert send_contract_renewal_reminders() == 0

        # Renewed to a later date: reminder re-arms once the new end enters the window.
        near = Institution.query.filter_by(name="Test University").one()
        near.contract_ends_at = datetime.now(UTC) + timedelta(days=25)
        db.session.commit()
        assert send_contract_renewal_reminders() == 1


def test_institution_recent_activity(client):
    from tests.conftest import register
    from institutions import institution_recent_activity

    register(client, email="dean@example.com", username="Dean Contributor")
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution()
        add_member(institution, user, role="admin")
        inst_id = institution.id

    upload_model_for(client, title="Activity Paper A")
    upload_model_for(client, title="Activity Paper B")

    with client.application.app_context():
        activity = institution_recent_activity(inst_id)
        assert len(activity["recent_members"]) == 1
        assert activity["recent_members"][0].user.email == "dean@example.com"
        assert len(activity["recent_models"]) == 2
        assert activity["models_last_30d"] == 2
        assert activity["top_contributors"][0]["user"].email == "dean@example.com"
        assert activity["top_contributors"][0]["model_count"] == 2


def test_institution_usage_excludes_soft_deleted_papers(client):
    from tests.conftest import register

    register(client)
    with client.application.app_context():
        from models import User

        user = User.query.one()
        institution = create_institution()
        add_member(institution, user)

    model_id = upload_model_for(client)
    with client.application.app_context():
        institution = Institution.query.one()
        count, _ = institution_usage(institution.id)
        assert count == 1

        model = db.session.get(Model3D, model_id)
        paper = db.session.get(Paper, model.paper_id)
        paper.status = "deleted"
        db.session.commit()

        count, used = institution_usage(institution.id)
        assert count == 0
        assert used == 0
