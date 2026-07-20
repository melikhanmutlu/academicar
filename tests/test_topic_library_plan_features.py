import io
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import text

from analytics import analytics_snapshot
from app import ensure_sqlite_schema
from licensing import (
    get_license_plan,
    model_upgrade_options,
    refresh_license_plan_cache,
)
from models import (
    AnalyticsEvent,
    Coupon,
    Institution,
    LicensePlanConfig,
    Model3D,
    Paper,
    Payment,
    ProjectArticle,
    ProjectAttachment,
    User,
    db,
)
from tests.conftest import (
    create_user,
    pdf_bytes,
    register,
    upload_file_bytes,
    valid_ascii_stl_bytes,
)


def _pptx_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
    return buffer.getvalue()


def test_legacy_sqlite_coupon_reservation_columns_are_self_healed(app):
    with app.app_context():
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE payments DROP COLUMN coupon_reservation_active"))
            connection.execute(text("ALTER TABLE coupons DROP COLUMN reservation_count"))

        ensure_sqlite_schema(app)

        with db.engine.begin() as connection:
            payment_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(payments)")).fetchall()
            }
            coupon_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(coupons)")).fetchall()
            }

        assert "coupon_reservation_active" in payment_columns
        assert "reservation_count" in coupon_columns


def _make_model(app, *, license_type="free", public=True, title="Topic"):
    with app.app_context():
        user = User.query.first() or create_user()
        project = Paper(title=title, slug=f"topic-{uuid.uuid4().hex[:8]}", user_id=user.id, is_public=public, visibility="public" if public else "private")
        db.session.add(project)
        db.session.flush()
        model = Model3D(id=str(uuid.uuid4()), paper_id=project.id, user_id=user.id, glb_path="model.glb", processing_status="ready", license_type=license_type)
        db.session.add(model)
        db.session.commit()
        return model.id, project.slug


def test_topic_can_save_multiple_optional_articles_and_materials(client, app):
    register(client)
    response = client.post(
        "/projects/new",
        data={
            "title": "Cardiac valve teaching",
            "visibility": "public",
            "field": "Medicine",
            "department": "Plastic Surgery",
            "article_title[]": ["Valve morphology", "Surgical outcomes"],
            "article_authors[]": ["A. Author", "B. Author"],
            "article_year[]": ["2024", "2025"],
            "article_doi[]": ["10.1/valve", "10.1/outcomes"],
            "article_pmid[]": ["111", "222"],
            "article_abstract[]": ["First", "Second"],
            "article_pdf[]": [
                upload_file_bytes(pdf_bytes(), "valve.pdf"),
                upload_file_bytes(pdf_bytes(), "outcomes.pdf"),
            ],
            "materials[]": [
                upload_file_bytes(pdf_bytes(), "lecture.pdf"),
                upload_file_bytes(_pptx_bytes(), "seminar.pptx"),
            ],
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Cardiac valve teaching" in response.data
    with app.app_context():
        project = Paper.query.filter_by(title="Cardiac valve teaching").one()
        assert project.department == "Plastic Surgery"
        assert [article.title for article in project.articles] == ["Valve morphology", "Surgical outcomes"]
        assert all(article.pdf_path for article in project.articles)
        assert {attachment.file_type for attachment in project.attachments} == {"pdf", "pptx"}
        assert project.authors == "A. Author"  # legacy API compatibility

    public_page = client.get("/p/" + project.slug)
    assert b"Valve morphology" in public_page.data
    assert b"Surgical outcomes" in public_page.data
    assert b"lecture" in public_page.data


def test_blank_first_article_is_not_persisted(client, app):
    register(client)
    client.post("/projects/new", data={"title": "Topic only", "article_title[]": ""}, follow_redirects=True)
    with app.app_context():
        project = Paper.query.filter_by(title="Topic only").one()
        assert project.articles == []


def test_admin_plan_capabilities_gate_viewer_and_api(client, app):
    register(client)
    model_id, _slug = _make_model(app)
    with app.app_context():
        plan = get_license_plan("free")
        row = LicensePlanConfig(
            key="free", label=plan.label, price_usd_cents=0,
            duration_days=plan.duration_days, storage_limit_bytes=plan.storage_limit_bytes,
            is_purchasable=True, features=[], max_models_per_project=1,
        )
        db.session.add(row)
        db.session.commit()
        refresh_license_plan_cache()

    viewer = client.get(f"/view/{model_id}")
    assert viewer.status_code == 200
    assert b'id="annotateBtn"' not in viewer.data
    assert b'<button id="arBtn"' not in viewer.data
    assert client.post(f"/models/{model_id}/annotations", json={"label": "X"}).status_code == 403
    assert client.post(f"/models/{model_id}/collage", data={}).status_code == 403


def test_free_model_limit_blocks_second_upload(client, app):
    register(client)
    client.post("/projects/new", data={"title": "Limited topic"}, follow_redirects=True)
    with app.app_context():
        slug = Paper.query.filter_by(title="Limited topic").one().slug
    for filename in ("one.stl", "two.stl"):
        response = client.post(
            f"/papers/{slug}/upload-model",
            data={"file": upload_file_bytes(valid_ascii_stl_bytes(), filename), "source_unit": "cm", "compliance_confirm": "yes"},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    assert b"allows 1 model" in response.data
    with app.app_context():
        assert Model3D.query.join(Paper).filter(Paper.slug == slug).count() == 1


def test_coupon_changes_checkout_amount_and_is_redeemed_once(client, app):
    register(client)
    model_id, _slug = _make_model(app)
    with app.app_context():
        coupon = Coupon(code="ACADEMIC25", percent_off=25, applicable_plan_keys=["academic"], is_active=True)
        db.session.add(coupon)
        db.session.commit()
    response = client.post(f"/models/{model_id}/upgrade/academic", data={"coupon_code": "academic25"}, follow_redirects=False)
    assert response.status_code in (302, 303)
    with app.app_context():
        payment = Payment.query.filter_by(model_id=model_id).one()
        assert payment.amount_before_discount == 990
        assert payment.discount_amount == 248
        assert payment.amount_kurus == 742
        assert payment.coupon_code == "ACADEMIC25"
        assert Coupon.query.one().redemption_count == 1


def test_discounted_amount_is_sent_to_paytr(client, app, monkeypatch):
    register(client)
    model_id, _slug = _make_model(app)
    captured = {}

    class Response:
        def json(self):
            return {"status": "success", "token": "discounted-checkout"}

    def fake_post(url, data, timeout):
        captured.update({"url": url, "data": data, "timeout": timeout})
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    app.config.update(
        PAYMENT_PROVIDER="paytr",
        PAYTR_MERCHANT_ID="merchant-id",
        PAYTR_MERCHANT_KEY="merchant-key",
        PAYTR_MERCHANT_SALT="merchant-salt",
    )
    with app.app_context():
        db.session.add(
            Coupon(
                code="PAYTR25",
                percent_off=25,
                applicable_plan_keys=["academic"],
                is_active=True,
            )
        )
        db.session.commit()

    response = client.post(
        f"/models/{model_id}/upgrade/academic",
        data={"coupon_code": "PAYTR25"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/discounted-checkout")
    assert captured["url"] == "https://www.paytr.com/odeme/api/get-token"
    assert captured["data"]["payment_amount"] == 742
    with app.app_context():
        payment = Payment.query.filter_by(model_id=model_id).one()
        assert payment.amount_before_discount == 990
        assert payment.discount_amount == 248
        assert payment.amount_kurus == 742
        assert payment.status == "pending"
        coupon = Coupon.query.filter_by(code="PAYTR25").one()
        assert coupon.redemption_count == 0
        assert coupon.reservation_count == 1
        assert payment.coupon_reservation_active is True


def test_admin_can_create_and_pause_coupon(client, app):
    register(client)
    with app.app_context():
        user = User.query.one()
        user.is_admin = True
        db.session.commit()

    response = client.post(
        "/admin/coupons",
        data={
            "code": "FACULTY20",
            "discount_type": "percent",
            "discount_value": "20",
            "max_redemptions": "50",
            "plan_keys": ["academic", "extended_archive"],
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    with app.app_context():
        coupon = Coupon.query.filter_by(code="FACULTY20").one()
        assert coupon.percent_off == 20
        assert coupon.max_redemptions == 50
        assert coupon.applicable_plan_keys == ["academic", "extended_archive"]
        coupon_id = coupon.id

    assert client.post(f"/admin/coupons/{coupon_id}/toggle").status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Coupon, coupon_id).is_active is False


def test_coupon_reservation_enforces_limit_and_settles_once(client, app):
    register(client)
    model_id, _slug = _make_model(app)
    with app.app_context():
        coupon = Coupon(
            code="ONLYONE",
            percent_off=10,
            applicable_plan_keys=["academic"],
            max_redemptions=1,
            is_active=True,
        )
        db.session.add(coupon)
        db.session.commit()
        coupon_id = coupon.id

        from app import active_coupon, reserve_coupon
        reserved, error = reserve_coupon(coupon_id, "academic")
        assert error is None
        payment = Payment(
            user_id=User.query.one().id,
            model_id=model_id,
            paper_id=db.session.get(Model3D, model_id).paper_id,
            plan_key="academic",
            amount_kurus=891,
            currency="USD",
            provider="development",
            provider_reference="reserved-order",
            status="pending",
            coupon_id=reserved.id,
            coupon_code=reserved.code,
            coupon_reservation_active=True,
        )
        db.session.add(payment)
        db.session.commit()
        assert active_coupon("ONLYONE", "academic")[1] == "Coupon redemption limit has been reached."
        payment_id = payment.id

    paid = client.post(
        "/payment/webhook/development",
        json={
            "status": "paid",
            "payment_id": payment_id,
            "plan_key": "academic",
            "model_id": model_id,
        },
    )
    assert paid.status_code == 200
    duplicate = client.post(
        "/payment/webhook/development",
        json={
            "status": "paid",
            "payment_id": payment_id,
            "plan_key": "academic",
            "model_id": model_id,
        },
    )
    assert duplicate.status_code == 200
    with app.app_context():
        coupon = db.session.get(Coupon, coupon_id)
        payment = db.session.get(Payment, payment_id)
        assert coupon.reservation_count == 0
        assert coupon.redemption_count == 1
        assert payment.coupon_reservation_active is False


def test_model_level_insights_include_engagement_and_segments(client, app):
    register(client)
    model_id, _slug = _make_model(app, license_type="academic")
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        for event_name in ("model_viewed", "model_viewed", "viewer_ar_started", "share_link_copied", "qr_scanned"):
            db.session.add(AnalyticsEvent(event_name=event_name, owner_user_id=model.user_id, model_id=model.id, project_id=model.paper_id, visitor_hash="v1", country_code="TR", device_type="mobile"))
        db.session.commit()
        snapshot = analytics_snapshot(model.user_id)
        metric = snapshot["model_metrics"][0]
        assert metric["views"] == 2
        assert metric["unique_visitors"] == 1
        assert metric["ar_starts"] == 1
        assert metric["qr_scans"] == 1
        assert metric["engagement_rate"] == 100.0
        assert metric["top_country"]["label"] == "TR"
    page = client.get("/insights")
    assert b"Model-level performance" in page.data
    assert b"Leading segment" in page.data


def test_institution_showcase_groups_articles_by_department(client, app):
    register(client)
    with app.app_context():
        user = User.query.one()
        institution = Institution(name="Faculty", slug="faculty", status="active")
        db.session.add(institution)
        db.session.flush()
        project = Paper(title="Knee models", slug="knee-models", user_id=user.id, is_public=True, visibility="public", department="Orthopaedics")
        db.session.add(project)
        db.session.flush()
        db.session.add(ProjectArticle(project_id=project.id, title="Knee reconstruction", authors="Clinic Team"))
        db.session.add(Model3D(id=str(uuid.uuid4()), paper_id=project.id, user_id=user.id, glb_path="model.glb", processing_status="ready", license_type="institutional", institution_id=institution.id))
        db.session.commit()
    page = client.get("/i/faculty")
    assert page.status_code == 200
    assert b"Publications by department" in page.data
    assert b"Orthopaedics" in page.data
    assert b"Knee reconstruction" in page.data


def test_viewer_source_contains_non_distorting_combined_render_and_safari_toolbar_fix():
    source = Path("templates/viewer.html").read_text(encoding="utf-8")
    assert "drawImageContained(ctx, img" in source
    assert "const useNativeIOSArButton = false" in source
    assert "await restoreCamera(cameraBeforeRotation)" in source
    assert "await viewer.updateFraming()" not in source
    assert "requestUpdate('orientation'" not in source
    assert "guardInactiveArSceneUpdate" in source
    assert "if (!arRenderer.isPresenting) return" in source
    assert "data-generate-shot-set disabled" in source
    assert "viewer.classList.contains('is-loaded')" in source
    assert "new MediaRecorder" in source
    assert "<span>{{ name }}</span>" in source


def test_institutional_model_cannot_enter_self_service_checkout(client, app):
    register(client)
    model_id, _slug = _make_model(app, license_type="institutional")
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        assert model_upgrade_options(model) == []

    page = client.get(f"/models/{model_id}/upgrade")
    assert page.status_code == 200
    assert b"Continue to payment" not in page.data
    response = client.post(
        f"/models/{model_id}/upgrade/academic",
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    with app.app_context():
        assert db.session.get(Model3D, model_id).license_type == "institutional"
        assert Payment.query.filter_by(model_id=model_id).count() == 0


def test_disabled_plan_features_block_material_color_and_usdz(client, app):
    register(client)
    model_id, slug = _make_model(app)
    with app.app_context():
        plan = get_license_plan("free")
        db.session.add(
            LicensePlanConfig(
                key="free",
                label=plan.label,
                price_usd_cents=0,
                duration_days=plan.duration_days,
                storage_limit_bytes=plan.storage_limit_bytes,
                is_purchasable=True,
                features=[],
                max_models_per_project=1,
            )
        )
        project = Paper.query.filter_by(slug=slug).one()
        db.session.add(
            ProjectAttachment(
                project_id=project.id,
                title="Slides",
                original_filename="slides.pdf",
                file_type="pdf",
                source_path="slides.pdf",
                preview_pdf_path="slides.pdf",
            )
        )
        db.session.commit()
        attachment_id = ProjectAttachment.query.one().id
        refresh_license_plan_cache()

    assert client.get(f"/p/{slug}/materials/{attachment_id}").status_code == 403
    assert client.post(f"/models/{model_id}/viewer-color", json={"color": "#D62828"}).status_code == 403
    assert client.get(f"/files/{model_id}/model.usdz").status_code == 403

    blocked = client.post(
        f"/projects/{slug}/edit",
        data={
            "title": "Topic",
            "project_type": "research_project",
            "workflow_stage": "in_progress",
            "visibility": "public",
            "materials[]": upload_file_bytes(pdf_bytes(), "extra.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"supporting materials are disabled" in blocked.data


def test_invalid_presentation_rolls_back_entire_project(client, app):
    register(client)
    response = client.post(
        "/projects/new",
        data={
            "title": "Atomic project",
            "project_type": "research_project",
            "workflow_stage": "in_progress",
            "visibility": "private",
            "materials[]": upload_file_bytes(b"not an office file", "broken.pptx"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"The project could not be saved" in response.data
    with app.app_context():
        assert Paper.query.filter_by(title="Atomic project").count() == 0


def test_failed_and_expired_models_do_not_consume_topic_capacity(client, app):
    register(client)
    with app.app_context():
        user = User.query.one()
        project = Paper(title="Capacity", slug="capacity", user_id=user.id)
        db.session.add(project)
        db.session.flush()
        db.session.add_all([
            Model3D(
                id=str(uuid.uuid4()), paper_id=project.id, user_id=user.id,
                glb_path="failed.glb", processing_status="failed",
                license_type="extended_archive",
            ),
            Model3D(
                id=str(uuid.uuid4()), paper_id=project.id, user_id=user.id,
                glb_path="expired.glb", processing_status="ready",
                license_type="extended_archive", license_status="expired",
            ),
        ])
        db.session.commit()
        from app import project_model_capacity
        capacity = project_model_capacity(project)
        assert capacity["used"] == 0
        assert capacity["plan"].key == "free"
        assert capacity["can_add"] is True


def test_missing_model_qr_is_regenerated_on_demand(client, app):
    register(client)
    model_id, _slug = _make_model(app)
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        model.qr_code_path = "missing-qr.png"
        db.session.commit()

    response = client.get(f"/qr-image/{model_id}")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        assert model.qr_code_path == f"qr_{model_id}.png"
        assert (Path(app.config["QR_FOLDER"]) / model.qr_code_path).is_file()


def test_department_aliases_share_one_institution_group(client, app):
    register(client)
    with app.app_context():
        user = User.query.one()
        institution = Institution(name="Alias Faculty", slug="alias-faculty", status="active")
        db.session.add(institution)
        db.session.flush()
        for index, department in enumerate(("Orthopaedics", "orthopedics"), start=1):
            project = Paper(
                title=f"Knee {index}", slug=f"knee-{index}", user_id=user.id,
                is_public=True, visibility="public", department=department,
            )
            db.session.add(project)
            db.session.flush()
            db.session.add(ProjectArticle(project_id=project.id, title=f"Article {index}"))
            db.session.add(Model3D(
                id=str(uuid.uuid4()), paper_id=project.id, user_id=user.id,
                glb_path=f"model-{index}.glb", processing_status="ready",
                license_type="institutional", institution_id=institution.id,
            ))
        db.session.commit()

    page = client.get("/i/alias-faculty")
    assert page.status_code == 200
    assert page.data.count(b'<h3>Orthopaedics</h3>') == 1
    assert b"2 publications" in page.data


def test_engagement_rate_uses_unique_engaged_visitors_and_is_capped(client, app):
    register(client)
    model_id, _slug = _make_model(app, license_type="academic")
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        for event_name in (
            "model_viewed",
            "viewer_ar_started", "viewer_ar_started", "viewer_ar_started",
            "share_link_copied", "share_link_copied",
        ):
            db.session.add(AnalyticsEvent(
                event_name=event_name, owner_user_id=model.user_id,
                model_id=model.id, project_id=model.paper_id,
                visitor_hash="same-visitor",
            ))
        db.session.commit()
        metric = analytics_snapshot(model.user_id)["model_metrics"][0]
        assert metric["engaged_visitors"] == 1
        assert metric["engagement_rate"] == 100.0


def test_browser_analytics_event_is_deduplicated(client, app):
    register(client)
    model_id, _slug = _make_model(app)
    first = client.post("/analytics/event", json={"event": "share_link_copied", "model_id": model_id})
    second = client.post("/analytics/event", json={"event": "share_link_copied", "model_id": model_id})
    assert first.status_code == 202
    assert second.status_code == 202
    assert second.get_json()["duplicate"] is True
    with app.app_context():
        assert AnalyticsEvent.query.filter_by(
            event_name="share_link_copied", model_id=model_id
        ).count() == 1
