"""Link previews (og:image etc.) for models and projects."""
import os
import re
import uuid
from datetime import UTC, datetime, timedelta

from models import Model3D, Paper, User, db
from tests.conftest import register


def _model(app, visibility="public", poster=True, expires=None):
    with app.app_context():
        owner = User.query.filter_by(email="user@example.com").first()
        paper = Paper(title="Share project", slug=f"share-{uuid.uuid4().hex[:8]}", user_id=owner.id,
                      visibility=visibility, is_public=visibility == "public", abstract="Spine study.")
        db.session.add(paper)
        db.session.flush()
        model = Model3D(id=str(uuid.uuid4()), paper_id=paper.id, user_id=owner.id, display_name="Vertebra",
                        glb_path="converted/x/model.glb", license_type="academic", processing_status="ready",
                        access_expires_at=expires)
        folder = os.path.join(app.config["CONVERTED_FOLDER"], model.id)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "model.glb"), "wb") as fh:
            fh.write(b"glTF")
        if poster:
            model.poster_path = os.path.join(folder, "poster.png")
            with open(model.poster_path, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
        db.session.add(model)
        db.session.commit()
        return model.id, paper.slug


def _meta(html, prop):
    found = re.findall(rf'<meta (?:property|name)="{re.escape(prop)}" content="([^"]*)"', html)
    return found


def test_viewer_preview_uses_the_model_poster(client, app):
    register(client)
    model_id, _ = _model(app)
    client.post("/auth/logout")
    html = client.get(f"/view/{model_id}").get_data(as_text=True)
    assert _meta(html, "og:image") == [_meta(html, "twitter:image")[0]]
    assert _meta(html, "og:image")[0].endswith(f"/files/{model_id}/poster.png")
    assert _meta(html, "twitter:card") == ["summary_large_image"]


def test_project_preview_has_one_title_and_the_model_poster(client, app):
    register(client)
    model_id, slug = _model(app)
    client.post("/auth/logout")
    html = client.get(f"/p/{slug}").get_data(as_text=True)
    assert _meta(html, "og:title") == ["Share project"]
    assert _meta(html, "og:description") == ["Spine study."]
    assert len(_meta(html, "og:image")) == 1
    assert _meta(html, "og:image")[0].endswith(f"/files/{model_id}/poster.png")


def test_preview_falls_back_when_crawlers_cannot_fetch_the_poster(client, app):
    register(client)
    _model_id, slug = _model(app, visibility="unlisted")
    expired_id, _ = _model(app, expires=datetime.now(UTC) - timedelta(days=1))
    with app.app_context():
        from app import share_image_url

        paper = Paper.query.filter_by(slug=slug).one()
        with app.test_request_context():
            assert share_image_url(paper).endswith("clinical-spatial-hero.png")
            assert share_image_url(model=db.session.get(Model3D, expired_id)).endswith("clinical-spatial-hero.png")


def test_model_card_offers_embed_code_for_public_projects_only(client, app):
    import html as html_lib

    register(client)
    model_id, slug = _model(app)
    page = client.get(f"/projects/{slug}").get_data(as_text=True)
    match = re.search(r"data-copy-link='([^']*)' aria-label=\"Copy embed code\"", page)
    assert match, "embed button missing"
    snippet = html_lib.unescape(match.group(1))
    assert snippet.startswith(f'<iframe src="http')
    assert f"/view/{model_id}?embed=true" in snippet
    assert 'title="Vertebra (interactive 3D model)"' in snippet

    _private_id, private_slug = _model(app, visibility="private")
    assert "Copy embed code" not in client.get(f"/projects/{private_slug}").get_data(as_text=True)


def test_embed_mode_accepts_true_and_1(client, app):
    register(client)
    model_id, _ = _model(app)
    client.post("/auth/logout")
    for flag in ("true", "1"):
        response = client.get(f"/view/{model_id}?embed={flag}")
        assert "X-Frame-Options" not in response.headers
        assert "Sleek Overlay for Embed" in response.get_data(as_text=True)


def test_print_quality_qr_downloads(client, app):
    import io

    from PIL import Image

    register(client)
    model_id, slug = _model(app)
    page = client.get(f"/qr-print/{model_id}").get_data(as_text=True)
    assert "Poster label (SVG)" in page

    svg = client.get(f"/qr-print/{model_id}/qr.svg")
    assert svg.status_code == 200 and svg.mimetype == "image/svg+xml"
    assert "attachment" in svg.headers["Content-Disposition"]
    assert svg.data.startswith(b"<?xml") and b"<path" in svg.data

    png = client.get(f"/qr-print/{model_id}/qr-print.png")
    assert Image.open(io.BytesIO(png.data)).size[0] >= 1500

    label = client.get(f"/qr-print/{model_id}/label.svg").get_data(as_text=True)
    assert "Scan to view in 3D &amp; AR" in label and "Vertebra" in label
    label_png = Image.open(io.BytesIO(client.get(f"/qr-print/{model_id}/label.png").data))
    assert label_png.size[0] == 1800

    with app.app_context():
        paper_id = Paper.query.filter_by(slug=slug).one().id
    assert client.get(f"/qr-print/paper/{paper_id}/label.svg").status_code == 200
    assert client.get(f"/qr-print/{model_id}/nope.exe").status_code == 404

    # Owner only.
    client.post("/auth/logout")
    register(client, email="other@example.com")
    assert client.get(f"/qr-print/{model_id}/qr.svg").status_code == 403


def test_qr_svg_decodes_to_the_resolver_url(app):
    """The vector QR encodes exactly the stored target (checked via the
    matrix the SVG is drawn from)."""
    from services.qr_assets import qr_matrix
    import qrcode

    url = "https://academicar.com/m/abc123"
    reference = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    reference.add_data(url)
    reference.make(fit=True)
    assert qr_matrix(url) == reference.get_matrix()
