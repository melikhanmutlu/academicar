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


def upload_model_for(client, title="Inst Paper", filename="model.stl"):
    """Create a paper and upload a valid STL to it; returns the Model3D id."""
    from tests.conftest import upload_file_bytes, valid_ascii_stl_bytes

    client.post("/papers/new", data={"title": title}, follow_redirects=True)
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
