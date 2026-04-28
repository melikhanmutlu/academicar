"""AcademicAR Flask application entry point."""
import logging
import os
import struct
import time
import uuid
from datetime import UTC, datetime

import qrcode
from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import LoginManager, current_user, login_required
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFError, CSRFProtect
from slugify import slugify
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from auth import auth_bp, init_oauth
from config import Config
from converters import STLConverter
from models import Model3D, Paper, User, db
from url_helpers import public_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
csrf = CSRFProtect()
upload_attempts: dict[int, list[float]] = {}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    validate_secret_key(app)
    Config.init_app(app)

    db.init_app(app)
    Migrate(app, db)
    csrf.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Bu sayfaya erişmek için giriş yapın."
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    init_oauth(app)
    app.register_blueprint(auth_bp)

    @app.context_processor
    def inject_globals():
        return {
            "current_year": datetime.now(UTC).year,
            "format_file_size": format_file_size,
            "public_url": public_url,
        }

    register_error_handlers(app)
    register_routes(app)

    with app.app_context():
        db.create_all()

    return app


def allowed_stl(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "stl"


def allowed_pdf(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "pdf"


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (TypeError, ValueError):
        return False


def human_file_size(limit_bytes: int) -> str:
    return f"{limit_bytes / (1024 * 1024):.0f} MB"


def format_file_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "0 B"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size_bytes} B"


def validate_secret_key(app: Flask) -> None:
    app_env = str(app.config.get("APP_ENV", "development")).lower()
    secret_key = app.config.get("SECRET_KEY")
    if app_env in {"production", "prod", "pilot"} and (
        not secret_key or secret_key == "dev-secret-change-in-production"
    ):
        raise RuntimeError("SECRET_KEY must be set for pilot/production environments.")


def check_upload_rate_limit(user_id: int, app: Flask) -> bool:
    limit = int(app.config.get("UPLOAD_RATE_LIMIT_COUNT", 5))
    window = int(app.config.get("UPLOAD_RATE_LIMIT_WINDOW", 600))
    if limit <= 0 or window <= 0:
        return True

    now = time.time()
    attempts = [ts for ts in upload_attempts.get(user_id, []) if now - ts < window]
    if len(attempts) >= limit:
        upload_attempts[user_id] = attempts
        return False
    attempts.append(now)
    upload_attempts[user_id] = attempts
    return True


def validate_stl_file(file_path: str) -> list[str]:
    """Return user-friendly STL validation errors before trimesh parsing."""
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return ["STL dosyası bulunamadı."]

    size = os.path.getsize(file_path)
    if size == 0:
        return ["STL dosyası boş."]
    if size < 15:
        return ["STL dosyası çok küçük veya bozuk görünüyor."]

    with open(file_path, "rb") as f:
        header = f.read(512)

    lower_header = header.lower()
    is_ascii_stl = lower_header.lstrip().startswith(b"solid") and (
        b"facet" in lower_header or b"endsolid" in lower_header
    )

    is_binary_stl = False
    if size >= 84:
        with open(file_path, "rb") as f:
            f.seek(80)
            triangle_count_raw = f.read(4)
        if len(triangle_count_raw) == 4:
            triangle_count = struct.unpack("<I", triangle_count_raw)[0]
            expected_size = 84 + triangle_count * 50
            is_binary_stl = triangle_count > 0 and expected_size <= size

    if not is_ascii_stl and not is_binary_stl:
        return ["STL başlığı tanınamadı veya dosya bozuk."]
    return []


def validate_pdf_file(file_path: str) -> list[str]:
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return ["PDF dosyası bulunamadı."]
    if os.path.getsize(file_path) == 0:
        return ["PDF dosyası boş."]
    with open(file_path, "rb") as f:
        if f.read(5) != b"%PDF-":
            return ["PDF dosyası geçerli bir PDF olarak görünmüyor."]
    return []


def make_slug(title: str) -> str:
    base = slugify(title)[:200] or "paper"
    slug = base
    counter = 1
    while Paper.query.filter_by(slug=slug).first() is not None:
        counter += 1
        slug = f"{base}-{counter}"
    return slug


def validate_paper_form(form) -> tuple[dict, list[str]]:
    title = (form.get("title") or "").strip()
    authors = (form.get("authors") or "").strip()
    field = (form.get("field") or "").strip()
    abstract = (form.get("abstract") or "").strip()
    doi = (form.get("doi") or "").strip()
    institution = (form.get("institution") or "").strip()
    year_raw = (form.get("year") or "").strip()
    errors = []

    if not title:
        errors.append("Baslik zorunludur.")
    elif len(title) > 500:
        errors.append("Baslik en fazla 500 karakter olabilir.")

    length_limits = {
        "Yazarlar": (authors, 500),
        "Alan": (field, 100),
        "DOI": (doi, 200),
        "Kurum / Dergi": (institution, 300),
    }
    for label, (value, limit) in length_limits.items():
        if len(value) > limit:
            errors.append(f"{label} en fazla {limit} karakter olabilir.")

    year_int = None
    if year_raw:
        try:
            year_int = int(year_raw)
        except ValueError:
            errors.append("Yil sayisal olmalidir.")
        else:
            max_year = datetime.now(UTC).year + 1
            if year_int < 1900 or year_int > max_year:
                errors.append(f"Yil 1900 ile {max_year} arasinda olmalidir.")

    return (
        {
            "title": title,
            "authors": authors or None,
            "year": year_int,
            "field": field or None,
            "abstract": abstract or None,
            "doi": doi or None,
            "institution": institution or None,
        },
        errors,
    )


def generate_qr(model_id: str, qr_folder: str, view_url: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(view_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    filename = f"qr_{model_id}.png"
    img.save(os.path.join(qr_folder, filename))
    return filename


def converter_message(converter: STLConverter, fallback: str) -> str:
    return "; ".join(converter.errors) if converter.errors else fallback


def cleanup_dir(path: str | None) -> None:
    if not path:
        return
    import shutil

    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
    except OSError as e:
        logger.warning("Failed to remove directory %s: %s", path, e)


def cleanup_file(path: str | None) -> None:
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning("Failed to remove file %s: %s", path, e)


def cleanup_model_files(app: Flask, model: Model3D) -> None:
    cleanup_paths(collect_model_file_paths(app, model))


def collect_model_file_paths(app: Flask, model: Model3D) -> list[tuple[str, str]]:
    paths = []
    if model.glb_path:
        paths.append(("dir", os.path.dirname(model.glb_path)))
    if model.qr_code_path:
        paths.append(("file", os.path.join(app.config["QR_FOLDER"], os.path.basename(model.qr_code_path))))
    return paths


def collect_paper_file_paths(app: Flask, paper: Paper) -> list[tuple[str, str]]:
    paths = []
    for model in paper.models:
        paths.extend(collect_model_file_paths(app, model))
    if paper.pdf_path:
        paths.append(("file", os.path.join(app.config["PDF_FOLDER"], paper.pdf_path)))
    return paths


def cleanup_paths(paths: list[tuple[str, str]]) -> None:
    for path_type, path in paths:
        if path_type == "dir":
            cleanup_dir(path)
        else:
            cleanup_file(path)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def csrf_error(error):
        flash("Oturum güvenlik doğrulaması başarısız oldu. Lütfen tekrar deneyin.", "danger")
        return render_template("errors/400.html", error=error), 400

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(error):
        limit = human_file_size(app.config["MAX_CONTENT_LENGTH"])
        flash(f"Dosya boyutu en fazla {limit} olabilir.", "danger")
        return render_template("errors/413.html", limit=limit), 413

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        logger.exception("Unhandled server error")
        return render_template("errors/500.html"), 500


def register_routes(app: Flask) -> None:
    @app.route("/")
    def landing():
        return render_template("landing.html")

    @app.route("/view/<model_id>")
    def view_model(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        return render_template("viewer.html", model=model, paper=model.paper)

    @app.route("/files/<unique_id>/<path:filename>")
    def serve_glb(unique_id, filename):
        if not is_uuid(unique_id) or filename != "model.glb":
            abort(404)
        model = db.session.get(Model3D, unique_id)
        if not model:
            abort(404)
        directory = os.path.join(app.config["CONVERTED_FOLDER"], unique_id)
        return send_from_directory(directory, "model.glb", mimetype="model/gltf-binary")

    @app.route("/qr-image/<model_id>")
    def qr_image(model_id):
        model = db.session.get(Model3D, model_id)
        if not model or not model.qr_code_path:
            abort(404)
        return send_from_directory(app.config["QR_FOLDER"], os.path.basename(model.qr_code_path))

    @app.route("/qr-print/<model_id>")
    def qr_print(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        return render_template("qr_page.html", model=model, paper=model.paper)

    @app.route("/pdfs/<int:paper_id>")
    @login_required
    def serve_pdf(paper_id):
        paper = db.session.get(Paper, paper_id)
        if not paper or not paper.pdf_path:
            abort(404)
        if paper.user_id != current_user.id:
            abort(403)
        return send_from_directory(app.config["PDF_FOLDER"], os.path.basename(paper.pdf_path))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        papers = Paper.query.filter_by(user_id=current_user.id).order_by(Paper.created_at.desc()).all()
        return render_template("dashboard.html", papers=papers)

    @app.route("/papers/new", methods=["GET", "POST"])
    @login_required
    def paper_new():
        if request.method == "POST":
            paper_data, paper_errors = validate_paper_form(request.form)
            if paper_errors:
                flash(" ".join(paper_errors), "danger")
                return render_template("paper_new.html", form=request.form)

            paper = Paper(
                title=paper_data["title"],
                authors=paper_data["authors"],
                year=paper_data["year"],
                field=paper_data["field"],
                abstract=paper_data["abstract"],
                doi=paper_data["doi"],
                institution=paper_data["institution"],
                slug=make_slug(paper_data["title"]),
                user_id=current_user.id,
            )

            saved_pdf_path = None
            pdf_file = request.files.get("pdf")
            if pdf_file and pdf_file.filename:
                if not allowed_pdf(pdf_file.filename):
                    flash("Sadece .pdf dosyaları kabul edilir.", "danger")
                    return render_template("paper_new.html", form=request.form)
                pdf_filename = f"{uuid.uuid4()}_{secure_filename(pdf_file.filename)}"
                saved_pdf_path = os.path.join(app.config["PDF_FOLDER"], pdf_filename)
                pdf_file.save(saved_pdf_path)
                pdf_errors = validate_pdf_file(saved_pdf_path)
                if pdf_errors:
                    cleanup_file(saved_pdf_path)
                    flash("PDF dosyası geçersiz: " + "; ".join(pdf_errors), "danger")
                    return render_template("paper_new.html", form=request.form)
                paper.pdf_path = pdf_filename

            try:
                db.session.add(paper)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                paper.slug = make_slug(f"{paper_data['title']}-{uuid.uuid4().hex[:6]}")
                db.session.add(paper)
                try:
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    cleanup_file(saved_pdf_path)
                    logger.exception("Paper create failed after slug retry")
                    flash("Tez kaydedilemedi. Lütfen tekrar deneyin.", "danger")
                    return render_template("paper_new.html", form=request.form)
            except SQLAlchemyError:
                db.session.rollback()
                cleanup_file(saved_pdf_path)
                logger.exception("Paper create failed")
                flash("Tez kaydedilemedi. Lütfen tekrar deneyin.", "danger")
                return render_template("paper_new.html", form=request.form)

            flash("Tez/makale eklendi.", "success")
            return redirect(url_for("paper_detail", slug=paper.slug))

        return render_template("paper_new.html", form={})

    @app.route("/papers/<slug>")
    @login_required
    def paper_detail(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper.user_id != current_user.id:
            abort(403)
        return render_template("paper_detail.html", paper=paper)

    @app.route("/papers/<slug>/delete", methods=["POST"])
    @login_required
    def paper_delete(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper.user_id != current_user.id:
            abort(403)
        file_paths = collect_paper_file_paths(app, paper)
        try:
            db.session.delete(paper)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Paper delete failed")
            flash("Tez silinemedi. Lütfen tekrar deneyin.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        cleanup_paths(file_paths)
        flash("Tez silindi.", "info")
        return redirect(url_for("dashboard"))

    @app.route("/papers/<slug>/upload-model", methods=["POST"])
    @login_required
    def upload_model(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper.user_id != current_user.id:
            abort(403)
        if not check_upload_rate_limit(current_user.id, app):
            flash("Çok kısa sürede fazla yükleme denemesi yaptınız. Lütfen birkaç dakika sonra tekrar deneyin.", "warning")
            return redirect(url_for("paper_detail", slug=slug))

        file = request.files.get("file")
        if not file or not file.filename:
            flash("Dosya seçilmedi.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        if not allowed_stl(file.filename):
            flash("Sadece .stl dosyaları kabul edilir.", "danger")
            return redirect(url_for("paper_detail", slug=slug))

        display_name = (request.form.get("display_name") or "").strip() or None
        description = (request.form.get("description") or "").strip() or None
        color = (request.form.get("color") or "").strip() or None
        unique_id = str(uuid.uuid4())
        original_name = secure_filename(file.filename)

        upload_dir = os.path.join(app.config["UPLOAD_FOLDER"], unique_id)
        converted_dir = os.path.join(app.config["CONVERTED_FOLDER"], unique_id)
        os.makedirs(upload_dir, exist_ok=True)
        os.makedirs(converted_dir, exist_ok=True)
        stl_path = os.path.join(upload_dir, original_name)
        glb_path = os.path.join(converted_dir, "model.glb")

        try:
            file.save(stl_path)
            stl_errors = validate_stl_file(stl_path)
            if stl_errors:
                flash("STL dosyası geçersiz: " + "; ".join(stl_errors), "danger")
                cleanup_dir(upload_dir)
                cleanup_dir(converted_dir)
                return redirect(url_for("paper_detail", slug=slug))

            converter = STLConverter()
            if not converter.validate(stl_path):
                flash("STL dosyası geçersiz: " + converter_message(converter, "Dosya okunamadı."), "danger")
                cleanup_dir(upload_dir)
                cleanup_dir(converted_dir)
                return redirect(url_for("paper_detail", slug=slug))

            success = converter.convert(stl_path, glb_path, color=color)
            if not success or not os.path.exists(glb_path):
                flash(
                    "Dönüşüm başarısız: "
                    + converter_message(converter, "STL dosyası GLB formatına dönüştürülemedi."),
                    "danger",
                )
                cleanup_dir(upload_dir)
                cleanup_dir(converted_dir)
                return redirect(url_for("paper_detail", slug=slug))

            cleanup_dir(upload_dir)
            view_url = public_url("view_model", model_id=unique_id)
            qr_filename = generate_qr(unique_id, app.config["QR_FOLDER"], view_url)
            model = Model3D(
                id=unique_id,
                paper_id=paper.id,
                user_id=current_user.id,
                display_name=display_name,
                description=description,
                original_filename=original_name,
                glb_path=glb_path,
                qr_code_path=qr_filename,
                file_size=os.path.getsize(glb_path),
            )
            db.session.add(model)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            cleanup_dir(upload_dir)
            cleanup_dir(converted_dir)
            cleanup_file(os.path.join(app.config["QR_FOLDER"], f"qr_{unique_id}.png"))
            logger.exception("Model save failed")
            flash("Model kaydedilemedi. Lütfen tekrar deneyin.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        except Exception:
            db.session.rollback()
            cleanup_dir(upload_dir)
            cleanup_dir(converted_dir)
            cleanup_file(os.path.join(app.config["QR_FOLDER"], f"qr_{unique_id}.png"))
            logger.exception("STL to GLB conversion failed")
            flash("Dönüşüm sırasında beklenmeyen bir hata oluştu. Lütfen dosyayı kontrol edip tekrar deneyin.", "danger")
            return redirect(url_for("paper_detail", slug=slug))

        flash("Model yüklendi ve QR kodu üretildi.", "success")
        return redirect(url_for("paper_detail", slug=slug))

    @app.route("/models/<model_id>/edit", methods=["GET", "POST"])
    @login_required
    def model_edit(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        if model.user_id != current_user.id:
            abort(403)
        if request.method == "POST":
            model.display_name = (request.form.get("display_name") or "").strip() or None
            model.description = (request.form.get("description") or "").strip() or None
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                logger.exception("Model edit failed")
                flash("Model bilgileri kaydedilemedi.", "danger")
                return render_template("model_edit.html", model=model, paper=model.paper)
            flash("Model bilgileri güncellendi.", "success")
            return redirect(url_for("paper_detail", slug=model.paper.slug))
        return render_template("model_edit.html", model=model, paper=model.paper)

    @app.route("/models/<model_id>/delete", methods=["POST"])
    @login_required
    def model_delete(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        if model.user_id != current_user.id:
            abort(403)
        slug = model.paper.slug
        file_paths = collect_model_file_paths(app, model)
        try:
            db.session.delete(model)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Model delete failed")
            flash("Model silinemedi. Lütfen tekrar deneyin.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        cleanup_paths(file_paths)
        flash("Model silindi.", "info")
        return redirect(url_for("paper_detail", slug=slug))


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=app.config.get("DEBUG", False))
