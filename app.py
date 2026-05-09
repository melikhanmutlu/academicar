"""AcademicAR Flask application entry point."""
import logging
import os
import struct
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import LoginManager, current_user, login_required
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFError, CSRFProtect
from slugify import slugify
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from auth import auth_bp, init_oauth
from config import Config
from converters import STLConverter
from converters.stl_converter import convert_glb_to_usdz
from models import AuditLog, Model3D, Paper, Payment, User, db
from url_helpers import public_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
csrf = CSRFProtect()
upload_attempts: dict[int, list[float]] = {}
conversion_executor = ThreadPoolExecutor(max_workers=2)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    upload_attempts.clear()
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    validate_secret_key(app)
    Config.init_app(app)
    if app.config.get("APP_ENV") in {"production", "prod", "pilot"}:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

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
        try:
            db.create_all()
        except OperationalError as exc:
            if "already exists" not in str(exc).lower():
                raise
            logger.warning("SQLite schema already existed during create_all; continuing with compatibility checks.")
        ensure_sqlite_schema()

    return app


def allowed_stl(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "stl"


def allowed_glb(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "glb"


def allowed_model(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in {"stl", "glb"}


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


def ensure_sqlite_schema() -> None:
    """Keep existing local SQLite databases usable until formal migrations run."""
    if db.engine.dialect.name != "sqlite":
        return

    paper_columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(papers)"))}
    model_columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(models)"))}
    user_columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(users)"))}
    paper_additions = {
        "package_type": "VARCHAR(30) DEFAULT 'temporary' NOT NULL",
        "status": "VARCHAR(30) DEFAULT 'active' NOT NULL",
        "is_public": "BOOLEAN DEFAULT 0 NOT NULL",
        "payment_status": "VARCHAR(30) DEFAULT 'free' NOT NULL",
        "payment_provider": "VARCHAR(50)",
        "payment_reference": "VARCHAR(200)",
        "pmid": "VARCHAR(100)",
        "expires_at": "DATETIME",
    }
    model_additions = {
        "source_format": "VARCHAR(10) DEFAULT 'stl' NOT NULL",
        "processing_status": "VARCHAR(30) DEFAULT 'ready' NOT NULL",
        "processing_error": "TEXT",
        "anonymization_confirmed": "BOOLEAN DEFAULT 0 NOT NULL",
        "rights_confirmed": "BOOLEAN DEFAULT 0 NOT NULL",
        "ethics_responsibility_confirmed": "BOOLEAN DEFAULT 0 NOT NULL",
        "consent_confirmed_at": "DATETIME",
        "consent_ip": "VARCHAR(100)",
    }
    user_additions = {
        "plan": "VARCHAR(30) DEFAULT 'free' NOT NULL",
    }

    for name, definition in paper_additions.items():
        if name not in paper_columns:
            db.session.execute(text(f"ALTER TABLE papers ADD COLUMN {name} {definition}"))
    for name, definition in model_additions.items():
        if name not in model_columns:
            db.session.execute(text(f"ALTER TABLE models ADD COLUMN {name} {definition}"))
    for name, definition in user_additions.items():
        if name not in user_columns:
            db.session.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))
    db.session.commit()


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
        return ["STL file was not found."]

    size = os.path.getsize(file_path)
    if size == 0:
        return ["STL file is empty."]
    if size < 15:
        return ["STL file is too small or appears to be corrupted."]

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
        return ["STL header could not be recognized or the file is corrupted."]
    return []


def validate_glb_file(file_path: str) -> list[str]:
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return ["GLB file was not found."]
    if os.path.getsize(file_path) < 20:
        return ["GLB file is empty or too small."]
    with open(file_path, "rb") as f:
        if f.read(4) != b"glTF":
            return ["GLB header is not valid."]
    return []


def validate_pdf_file(file_path: str) -> list[str]:
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return ["PDF file was not found."]
    if os.path.getsize(file_path) == 0:
        return ["PDF file is empty."]
    with open(file_path, "rb") as f:
        if f.read(5) != b"%PDF-":
            return ["PDF file does not appear to be a valid PDF."]
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
    pmid = (form.get("pmid") or "").strip()
    visibility = (form.get("visibility") or "public").strip().lower()
    year_raw = (form.get("year") or "").strip()
    errors = []

    if visibility not in {"public", "private"}:
        errors.append("Invalid visibility option.")

    if not title:
        errors.append("Title is required.")
    elif len(title) > 500:
        errors.append("Title can be at most 500 characters.")

    length_limits = {
        "Authors": (authors, 500),
        "Field": (field, 100),
        "DOI": (doi, 200),
        "Institution / Journal": (institution, 300),
        "PMID": (pmid, 100),
    }
    for label, (value, limit) in length_limits.items():
        if len(value) > limit:
            errors.append(f"{label} can be at most {limit} characters.")

    year_int = None
    if year_raw:
        try:
            year_int = int(year_raw)
        except ValueError:
            errors.append("Year must be numeric.")
        else:
            max_year = datetime.now(UTC).year + 1
            if year_int < 1900 or year_int > max_year:
                errors.append(f"Year must be between 1900 and {max_year}.")

    return (
        {
            "title": title,
            "authors": authors or None,
            "year": year_int,
            "field": field or None,
            "abstract": abstract or None,
            "doi": doi or None,
            "institution": institution or None,
            "pmid": pmid or None,
            "is_public": visibility == "public",
        },
        errors,
    )


def package_expires_at(package_type: str) -> datetime:
    if package_type == "academic":
        return datetime.now(UTC) + timedelta(days=365 * 3)
    return datetime.now(UTC) + timedelta(days=3)


def sync_paper_entitlements(paper: Paper) -> None:
    """Keep package lifetime and payment state enforceable in the database."""
    if paper.package_type == "academic":
        paper.payment_status = paper.payment_status or "paid"
        if not paper.expires_at:
            paper.expires_at = package_expires_at("academic")
    else:
        paper.package_type = "temporary"
        paper.payment_status = "free"
        if not paper.expires_at:
            paper.expires_at = package_expires_at("temporary")


def paper_is_expired(paper: Paper) -> bool:
    if not paper.expires_at:
        return False
    expires_at = paper.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < datetime.now(UTC)


def build_invoice_number(payment_id: int) -> str:
    return f"AAR-{datetime.now(UTC).strftime('%Y%m')}-{payment_id:05d}"


def generate_qr(model_id: str, qr_folder: str, view_url: str) -> str:
    import qrcode

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


def paper_qr_filename(paper_id: int) -> str:
    return f"qr_paper_{paper_id}.png"


def ensure_paper_qr(paper: Paper) -> str:
    """Lazily generate the paper-level QR (encoding the public landing URL)
    if it doesn't yet exist on disk. Returns the filename."""
    import qrcode

    qr_folder = current_app_qr_folder()
    filename = paper_qr_filename(paper.id)
    full_path = os.path.join(qr_folder, filename)
    if os.path.exists(full_path):
        return filename

    target_url = public_url("paper_public", slug=paper.slug)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img.save(full_path)
    return filename


def current_app_qr_folder() -> str:
    from flask import current_app

    return current_app.config["QR_FOLDER"]


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
    paths.append(("file", os.path.join(app.config["QR_FOLDER"], paper_qr_filename(paper.id))))
    return paths


def mark_model_failed(model_id: str, message: str) -> None:
    model = db.session.get(Model3D, model_id)
    if not model:
        return
    model.processing_status = "failed"
    model.processing_error = message[:2000]
    db.session.commit()


def process_model_upload_job(
    app: Flask,
    *,
    model_id: str,
    upload_dir: str,
    converted_dir: str,
    source_path: str,
    glb_path: str,
    usdz_path: str,
    source_format: str,
    color: str | None,
    source_unit: str,
) -> None:
    """Convert/copy the uploaded model in a background worker and update state."""
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        if not model:
            cleanup_dir(upload_dir)
            cleanup_dir(converted_dir)
            return

        model.processing_status = "processing"
        model.processing_error = None
        db.session.commit()

        try:
            if source_format == "glb":
                glb_errors = validate_glb_file(source_path)
                if glb_errors:
                    mark_model_failed(model_id, "Invalid GLB file: " + "; ".join(glb_errors))
                    cleanup_dir(upload_dir)
                    return
                os.replace(source_path, glb_path)
            else:
                stl_errors = validate_stl_file(source_path)
                if stl_errors:
                    mark_model_failed(model_id, "Invalid STL file: " + "; ".join(stl_errors))
                    cleanup_dir(upload_dir)
                    return

                converter = STLConverter()
                try:
                    success = converter.convert(
                        source_path, glb_path, color=color, source_unit=source_unit
                    )
                except TypeError:
                    success = converter.convert(source_path, glb_path, color=color)
                if not success or not os.path.exists(glb_path):
                    mark_model_failed(
                        model_id,
                        "Conversion failed: "
                        + converter_message(converter, "The file could not be converted to GLB."),
                    )
                    cleanup_dir(upload_dir)
                    return

            if os.path.exists(glb_path) and not os.path.exists(usdz_path):
                convert_glb_to_usdz(glb_path, usdz_path)

            cleanup_dir(upload_dir)
            site_url = (app.config.get("SITE_URL") or "http://localhost:5000").rstrip("/")
            view_url = f"{site_url}/view/{model_id}"
            qr_filename = generate_qr(model_id, app.config["QR_FOLDER"], view_url)

            model = db.session.get(Model3D, model_id)
            if not model:
                return
            model.file_size = os.path.getsize(glb_path)
            model.qr_code_path = qr_filename
            model.processing_status = "ready"
            model.processing_error = None
            db.session.add(
                AuditLog(
                    event_type="model_processed",
                    user_id=model.user_id,
                    resource_id=model_id,
                    details={"source_format": source_format},
                )
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.exception("Background model processing failed")
            mark_model_failed(model_id, "Unexpected conversion error. Please check the file and try again.")
            cleanup_dir(upload_dir)


def cleanup_paths(paths: list[tuple[str, str]]) -> None:
    for path_type, path in paths:
        if path_type == "dir":
            cleanup_dir(path)
        else:
            cleanup_file(path)


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def csrf_error(error):
        flash("Session security validation failed. Please try again.", "danger")
        return render_template("errors/400.html", error=error), 400

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(error):
        limit = human_file_size(app.config["MAX_CONTENT_LENGTH"])
        flash(f"File size can be at most {limit}.", "danger")
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


def log_audit(event_type: str, user_id: int | None = None, resource_id: str | None = None, details: dict | None = None) -> None:
    """Log an audit event for compliance tracking (KVKK/GDPR)."""
    try:
        ip_address = request.headers.get("X-Forwarded-For", request.remote_addr) if request else None
        audit_log = AuditLog(
            event_type=event_type,
            user_id=user_id,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
        )
        db.session.add(audit_log)
        db.session.commit()
    except Exception as e:
        logger.exception(f"Failed to log audit event {event_type}: {e}")
        db.session.rollback()


def register_routes(app: Flask) -> None:
    @app.route("/")
    def landing():
        return render_template("landing.html")

    @app.route("/terms")
    def terms():
        return render_template("legal/terms.html")

    @app.route("/privacy")
    def privacy():
        return render_template("legal/privacy.html")

    @app.route("/kvkk")
    def kvkk():
        return render_template("legal/kvkk.html")

    @app.route("/view/<model_id>")
    def view_model(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        if paper_is_expired(model.paper):
            abort(404)
        usdz_path = os.path.join(
            app.config["CONVERTED_FOLDER"], model.id, "model.usdz"
        )
        has_usdz = os.path.exists(usdz_path)
        return render_template(
            "viewer.html", model=model, paper=model.paper, has_usdz=has_usdz
        )

    @app.route("/files/<unique_id>/<path:filename>")
    def serve_glb(unique_id, filename):
        if not is_uuid(unique_id) or filename not in {"model.glb", "model.usdz"}:
            abort(404)
        model = db.session.get(Model3D, unique_id)
        if not model:
            abort(404)
        if paper_is_expired(model.paper):
            abort(404)
        directory = os.path.join(app.config["CONVERTED_FOLDER"], unique_id)
        if not os.path.exists(os.path.join(directory, filename)):
            abort(404)
        mimetype = (
            "model/vnd.usdz+zip" if filename == "model.usdz" else "model/gltf-binary"
        )
        response = send_from_directory(directory, filename, mimetype=mimetype)
        # Discourage casual download/caching by browsers and crawlers. The browser
        # still needs the bytes to render, so this is friction, not a hard barrier.
        response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    @app.route("/qr-image/<model_id>")
    def qr_image(model_id):
        model = db.session.get(Model3D, model_id)
        if not model or not model.qr_code_path:
            abort(404)
        if paper_is_expired(model.paper):
            abort(404)
        return send_from_directory(app.config["QR_FOLDER"], os.path.basename(model.qr_code_path))

    @app.route("/qr-print/<model_id>")
    @login_required
    def qr_print(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        if model.user_id != current_user.id:
            abort(403)
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

    def _paper_visible_to_request(paper: Paper) -> bool:
        """A paper is visible if it is public, or if the current user is the owner."""
        if paper.is_public:
            return True
        return current_user.is_authenticated and current_user.id == paper.user_id

    @app.route("/qr-image/paper/<int:paper_id>")
    def qr_image_paper(paper_id):
        paper = db.session.get(Paper, paper_id)
        if not paper:
            abort(404)
        if paper_is_expired(paper):
            abort(404)
        if not _paper_visible_to_request(paper):
            abort(404)
        filename = ensure_paper_qr(paper)
        return send_from_directory(app.config["QR_FOLDER"], filename)

    @app.route("/qr-print/paper/<int:paper_id>")
    @login_required
    def qr_print_paper(paper_id):
        paper = db.session.get(Paper, paper_id)
        if not paper:
            abort(404)
        if paper.user_id != current_user.id:
            abort(403)
        ensure_paper_qr(paper)
        return render_template("qr_page_paper.html", paper=paper)

    @app.route("/p/<slug>")
    def paper_public(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper_is_expired(paper):
            abort(404)
        if not _paper_visible_to_request(paper):
            abort(404)
        return render_template("paper_public.html", paper=paper)

    @app.route("/p/<slug>/pdf")
    def paper_public_pdf(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper_is_expired(paper):
            abort(404)
        if not _paper_visible_to_request(paper):
            abort(404)
        if not paper.pdf_path:
            abort(404)
        return render_template("pdf_reader.html", paper=paper)

    @app.route("/p/<slug>/pdf/file")
    def paper_public_pdf_file(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper_is_expired(paper):
            abort(404)
        if not _paper_visible_to_request(paper):
            abort(404)
        if not paper.pdf_path:
            abort(404)
        response = send_from_directory(
            app.config["PDF_FOLDER"],
            os.path.basename(paper.pdf_path),
            mimetype="application/pdf",
        )
        # Inline so the iframe can render it; discourage indexing.
        response.headers["Content-Disposition"] = 'inline; filename="paper.pdf"'
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @app.route("/dashboard")
    @login_required
    def dashboard():
        papers = Paper.query.filter_by(user_id=current_user.id).order_by(Paper.created_at.desc()).all()
        return render_template("dashboard.html", papers=papers)

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        if request.method == "POST":
            new_plan = (request.form.get("plan") or "").strip().lower()
            if new_plan not in {"free", "academic"}:
                flash("Invalid plan choice.", "danger")
                return redirect(url_for("profile"))
            previous = current_user.plan or "free"
            if new_plan == previous:
                flash("You're already on this plan.", "info")
                return redirect(url_for("profile"))

            current_user.plan = new_plan
            try:
                payment = None
                if new_plan == "academic":
                    payment = Payment(
                        user_id=current_user.id,
                        amount_kurus=50000,
                        currency="TRY",
                        provider="development",
                        provider_reference=f"dev-{uuid.uuid4().hex[:10]}",
                        status="paid",
                        paid_at=datetime.now(UTC),
                    )
                    db.session.add(payment)
                    db.session.flush()
                    payment.invoice_number = build_invoice_number(payment.id)
                db.session.commit()
                log_audit(
                    "plan_changed",
                    user_id=current_user.id,
                    details={
                        "from": previous,
                        "to": new_plan,
                        "payment_id": payment.id if payment else None,
                        "invoice_number": payment.invoice_number if payment else None,
                    },
                )
                if new_plan == "academic":
                    flash(
                        "You're now on the Academic plan. New publications will be created with persistent (3-year) viewer links and multi-model uploads.",
                        "success",
                    )
                else:
                    flash(
                        "Switched to the Free plan. New publications will be created as Temporary (3-day links, single model). Existing publications keep their current settings.",
                        "info",
                    )
            except SQLAlchemyError:
                db.session.rollback()
                flash("Could not update plan. Please try again.", "danger")
            return redirect(url_for("profile"))

        # Profile statistics
        user_papers = Paper.query.filter_by(user_id=current_user.id).all()
        paper_count = len(user_papers)
        academic_paper_count = sum(1 for p in user_papers if p.package_type == "academic")
        temporary_paper_count = paper_count - academic_paper_count
        public_paper_count = sum(1 for p in user_papers if p.is_public)
        private_paper_count = paper_count - public_paper_count
        pdf_paper_count = sum(1 for p in user_papers if p.pdf_path)
        model_count = sum(len(p.models) for p in user_papers)
        recent_payments = (
            Payment.query.filter_by(user_id=current_user.id)
            .order_by(Payment.created_at.desc())
            .limit(5)
            .all()
        )
        now = datetime.now(UTC)
        expiring_soon = 0
        for p in user_papers:
            if not p.expires_at or p.package_type == "academic":
                continue
            exp = p.expires_at if p.expires_at.tzinfo else p.expires_at.replace(tzinfo=UTC)
            delta = exp - now
            if 0 < delta.total_seconds() <= 86400 * 7:  # within 7 days
                expiring_soon += 1

        return render_template(
            "profile.html",
            user=current_user,
            paper_count=paper_count,
            academic_paper_count=academic_paper_count,
            temporary_paper_count=temporary_paper_count,
            public_paper_count=public_paper_count,
            private_paper_count=private_paper_count,
            pdf_paper_count=pdf_paper_count,
            model_count=model_count,
            expiring_soon=expiring_soon,
            has_password=bool(current_user.password_hash),
            recent_payments=recent_payments,
        )

    @app.route("/account/password", methods=["POST"])
    @login_required
    def account_change_password():
        current_pw = request.form.get("current_password") or ""
        new_pw = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not current_user.password_hash:
            flash(
                "Your account uses Google sign-in. Set a password from your Google account.",
                "warning",
            )
            return redirect(url_for("profile"))
        if not current_user.check_password(current_pw):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("profile"))
        if len(new_pw) < 6:
            flash("New password must be at least 6 characters.", "danger")
            return redirect(url_for("profile"))
        if new_pw != confirm:
            flash("New password and confirmation do not match.", "danger")
            return redirect(url_for("profile"))
        if new_pw == current_pw:
            flash("New password must be different from the current one.", "warning")
            return redirect(url_for("profile"))

        current_user.set_password(new_pw)
        try:
            db.session.commit()
            log_audit("password_changed", user_id=current_user.id)
            flash("Password updated.", "success")
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update password. Please try again.", "danger")
        return redirect(url_for("profile"))

    @app.route("/account/email", methods=["POST"])
    @login_required
    def account_change_email():
        new_email = (request.form.get("new_email") or "").strip().lower()
        password = request.form.get("current_password") or ""

        if not new_email or "@" not in new_email or len(new_email) > 120:
            flash("Please enter a valid email address.", "danger")
            return redirect(url_for("profile"))
        if new_email == (current_user.email or "").lower():
            flash("That is already your email address.", "info")
            return redirect(url_for("profile"))
        if current_user.password_hash and not current_user.check_password(password):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("profile"))
        if User.query.filter(User.email == new_email, User.id != current_user.id).first():
            flash("That email is already in use by another account.", "danger")
            return redirect(url_for("profile"))

        previous = current_user.email
        current_user.email = new_email
        try:
            db.session.commit()
            log_audit(
                "email_changed",
                user_id=current_user.id,
                details={"from": previous, "to": new_email},
            )
            flash("Email updated.", "success")
        except (IntegrityError, SQLAlchemyError):
            db.session.rollback()
            flash("Could not update email. Please try again.", "danger")
        return redirect(url_for("profile"))

    @app.route("/account/profile", methods=["POST"])
    @login_required
    def account_update_profile():
        username = (request.form.get("username") or "").strip()
        if len(username) < 2 or len(username) > 80:
            flash("Full name must be between 2 and 80 characters.", "danger")
            return redirect(url_for("profile"))

        previous = current_user.username
        current_user.username = username
        try:
            db.session.commit()
            log_audit(
                "profile_updated",
                user_id=current_user.id,
                details={"username_changed": previous != username},
            )
            flash("Profile information updated.", "success")
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update profile information. Please try again.", "danger")
        return redirect(url_for("profile"))

    @app.route("/account/delete", methods=["POST"])
    @login_required
    def account_delete():
        confirm = (request.form.get("confirm") or "").strip()
        password = request.form.get("current_password") or ""
        if confirm != "DELETE":
            flash('Type DELETE in the confirmation box to proceed.', "danger")
            return redirect(url_for("profile"))
        if current_user.password_hash and not current_user.check_password(password):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("profile"))

        # Cleanup: collect every file path tied to the user before the cascade
        # delete removes the database rows.
        files_to_remove = []
        for paper in current_user.papers:
            files_to_remove.extend(collect_paper_file_paths(app, paper))

        user_id = current_user.id
        user_email = current_user.email
        try:
            db.session.delete(current_user)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Account deletion failed")
            flash("Could not delete account. Please try again.", "danger")
            return redirect(url_for("profile"))

        cleanup_paths(files_to_remove)
        log_audit("account_deleted", user_id=user_id, details={"email": user_email})
        from flask_login import logout_user
        logout_user()
        flash("Your account and all associated data were permanently deleted.", "info")
        return redirect(url_for("landing"))

    @app.route("/papers/new", methods=["GET", "POST"])
    @login_required
    def paper_new():
        if request.method == "POST":
            paper_data, paper_errors = validate_paper_form(request.form)
            if paper_errors:
                flash(" ".join(paper_errors), "danger")
                return render_template("paper_new.html", form=request.form)

            # Plan is now a user-level setting (managed on /profile). New
            # papers inherit the user's current plan as a snapshot — this is
            # what determines expiration and multi-model upload limits.
            user_plan = current_user.plan or "free"
            inherited_package = "academic" if user_plan == "academic" else "temporary"
            paper = Paper(
                title=paper_data["title"],
                authors=paper_data["authors"],
                year=paper_data["year"],
                field=paper_data["field"],
                abstract=paper_data["abstract"],
                doi=paper_data["doi"],
                institution=paper_data["institution"],
                pmid=paper_data["pmid"],
                package_type=inherited_package,
                is_public=paper_data["is_public"],
                payment_status="paid" if inherited_package == "academic" else "free",
                expires_at=package_expires_at(inherited_package),
                slug=make_slug(paper_data["title"]),
                user_id=current_user.id,
            )

            saved_pdf_path = None
            pdf_file = request.files.get("pdf")
            if pdf_file and pdf_file.filename:
                if not allowed_pdf(pdf_file.filename):
                    flash("Only .pdf files are accepted.", "danger")
                    return render_template("paper_new.html", form=request.form)
                pdf_filename = f"{uuid.uuid4()}_{secure_filename(pdf_file.filename)}"
                saved_pdf_path = os.path.join(app.config["PDF_FOLDER"], pdf_filename)
                pdf_file.save(saved_pdf_path)
                pdf_errors = validate_pdf_file(saved_pdf_path)
                if pdf_errors:
                    cleanup_file(saved_pdf_path)
                    flash("Invalid PDF file: " + "; ".join(pdf_errors), "danger")
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
                    flash("The publication could not be saved. Please try again.", "danger")
                    return render_template("paper_new.html", form=request.form)
            except SQLAlchemyError:
                db.session.rollback()
                cleanup_file(saved_pdf_path)
                logger.exception("Paper create failed")
                flash("The publication could not be saved. Please try again.", "danger")
                return render_template("paper_new.html", form=request.form)

            flash("Publication created.", "success")
            return redirect(url_for("paper_detail", slug=paper.slug))

        return render_template("paper_new.html", form={})

    @app.route("/papers/<slug>")
    @login_required
    def paper_detail(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper.user_id != current_user.id:
            abort(403)
        return render_template("paper_detail.html", paper=paper)

    @app.route("/papers/<slug>/edit", methods=["GET", "POST"])
    @login_required
    def paper_edit(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper.user_id != current_user.id:
            abort(403)

        if request.method == "POST":
            paper_data, paper_errors = validate_paper_form(request.form)
            if paper_errors:
                flash(" ".join(paper_errors), "danger")
                return render_template("paper_new.html", form=request.form, paper=paper, mode="edit")

            paper.title = paper_data["title"]
            paper.authors = paper_data["authors"]
            paper.year = paper_data["year"]
            paper.field = paper_data["field"]
            paper.abstract = paper_data["abstract"]
            paper.doi = paper_data["doi"]
            paper.institution = paper_data["institution"]
            paper.pmid = paper_data["pmid"]
            # paper.package_type is locked at creation (snapshot of the user's
            # plan at that moment). Change the plan via /profile, not here.
            paper.is_public = paper_data["is_public"]
            if not paper.expires_at:
                paper.expires_at = package_expires_at(paper.package_type or "temporary")

            saved_pdf_path = None
            old_pdf_path = None
            pdf_file = request.files.get("pdf")
            if pdf_file and pdf_file.filename:
                if not allowed_pdf(pdf_file.filename):
                    flash("Only .pdf files are accepted.", "danger")
                    return render_template("paper_new.html", form=request.form, paper=paper, mode="edit")
                pdf_filename = f"{uuid.uuid4()}_{secure_filename(pdf_file.filename)}"
                saved_pdf_path = os.path.join(app.config["PDF_FOLDER"], pdf_filename)
                pdf_file.save(saved_pdf_path)
                pdf_errors = validate_pdf_file(saved_pdf_path)
                if pdf_errors:
                    cleanup_file(saved_pdf_path)
                    flash("Invalid PDF file: " + "; ".join(pdf_errors), "danger")
                    return render_template("paper_new.html", form=request.form, paper=paper, mode="edit")
                if paper.pdf_path:
                    old_pdf_path = os.path.join(app.config["PDF_FOLDER"], os.path.basename(paper.pdf_path))
                paper.pdf_path = pdf_filename

            try:
                db.session.commit()
                log_audit("paper_updated", user_id=current_user.id, resource_id=str(paper.id))
            except SQLAlchemyError:
                db.session.rollback()
                cleanup_file(saved_pdf_path)
                logger.exception("Paper update failed")
                flash("The publication could not be updated. Please try again.", "danger")
                return render_template("paper_new.html", form=request.form, paper=paper, mode="edit")

            cleanup_file(old_pdf_path)
            flash("Publication updated.", "success")
            return redirect(url_for("paper_detail", slug=paper.slug))

        form = {
            "title": paper.title or "",
            "authors": paper.authors or "",
            "year": paper.year or "",
            "field": paper.field or "",
            "institution": paper.institution or "",
            "doi": paper.doi or "",
            "pmid": paper.pmid or "",
            "abstract": paper.abstract or "",
            "visibility": "public" if paper.is_public else "private",
        }
        return render_template("paper_new.html", form=form, paper=paper, mode="edit")

    @app.route("/papers/<slug>/delete", methods=["POST"])
    @login_required
    def paper_delete(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper.user_id != current_user.id:
            abort(403)
        file_paths = collect_paper_file_paths(app, paper)
        paper_id = paper.id
        try:
            db.session.delete(paper)
            db.session.commit()
            log_audit("paper_deleted", user_id=current_user.id, resource_id=str(paper_id))
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Paper delete failed")
            flash("The publication could not be deleted. Please try again.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        cleanup_paths(file_paths)
        flash("Publication deleted.", "info")
        return redirect(url_for("dashboard"))

    @app.route("/papers/<slug>/upload-model", methods=["POST"])
    @login_required
    def upload_model(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        if paper.user_id != current_user.id:
            abort(403)
        if not check_upload_rate_limit(current_user.id, app):
            flash("Too many upload attempts in a short time. Please try again in a few minutes.", "warning")
            return redirect(url_for("paper_detail", slug=slug))

        file = request.files.get("file")
        if not file or not file.filename:
            flash("No file selected.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        if not allowed_model(file.filename):
            flash("Only .stl and .glb files are accepted in this MVP.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        if request.form.get("compliance_confirm") != "yes":
            flash(
                "You must confirm that the model is anonymized and that you have the right to share it.",
                "danger",
            )
            return redirect(url_for("paper_detail", slug=slug))
        if paper.package_type == "temporary" and paper.models:
            flash("Temporary publications support one model. Upgrade to add multiple models.", "warning")
            return redirect(url_for("paper_detail", slug=slug))

        display_name = (request.form.get("display_name") or "").strip() or None
        description = (request.form.get("description") or "").strip() or None
        color = (request.form.get("color") or "").strip() or None
        source_unit = (request.form.get("source_unit") or "auto").strip().lower()
        if source_unit not in {"auto", "mm", "cm", "m"}:
            source_unit = "auto"
        unique_id = str(uuid.uuid4())
        original_name = secure_filename(file.filename)
        source_format = original_name.rsplit(".", 1)[1].lower()

        upload_dir = os.path.join(app.config["UPLOAD_FOLDER"], unique_id)
        converted_dir = os.path.join(app.config["CONVERTED_FOLDER"], unique_id)
        os.makedirs(upload_dir, exist_ok=True)
        os.makedirs(converted_dir, exist_ok=True)
        stl_path = os.path.join(upload_dir, original_name)
        glb_path = os.path.join(converted_dir, "model.glb")
        usdz_path = os.path.join(converted_dir, "model.usdz")

        try:
            file.save(stl_path)
            preflight_errors = (
                validate_glb_file(stl_path)
                if source_format == "glb"
                else validate_stl_file(stl_path)
            )
            if preflight_errors:
                cleanup_dir(upload_dir)
                cleanup_dir(converted_dir)
                flash(
                    f"Invalid {source_format.upper()} file: " + "; ".join(preflight_errors),
                    "danger",
                )
                return redirect(url_for("paper_detail", slug=slug))
            usdz_file = request.files.get("usdz")
            if usdz_file and usdz_file.filename and usdz_file.filename.lower().endswith(".usdz"):
                try:
                    usdz_file.save(usdz_path)
                    logger.info("User-provided USDZ stored at %s", usdz_path)
                except OSError as e:
                    logger.warning("Failed to save user USDZ: %s", e)

            model = Model3D(
                id=unique_id,
                paper_id=paper.id,
                user_id=current_user.id,
                display_name=display_name,
                description=description,
                original_filename=original_name,
                glb_path=glb_path,
                qr_code_path=None,
                file_size=os.path.getsize(stl_path),
                source_format=source_format,
                processing_status="queued",
                anonymization_confirmed=True,
                rights_confirmed=True,
                ethics_responsibility_confirmed=True,
                consent_confirmed_at=datetime.now(UTC),
                consent_ip=request.headers.get("X-Forwarded-For", request.remote_addr),
                terms_version=app.config.get("TERMS_VERSION", "1.0"),
            )
            db.session.add(model)
            db.session.commit()
            job_kwargs = {
                "model_id": unique_id,
                "upload_dir": upload_dir,
                "converted_dir": converted_dir,
                "source_path": stl_path,
                "glb_path": glb_path,
                "usdz_path": usdz_path,
                "source_format": source_format,
                "color": color,
                "source_unit": source_unit,
            }
            if app.config.get("TESTING"):
                process_model_upload_job(app, **job_kwargs)
            else:
                conversion_executor.submit(process_model_upload_job, app, **job_kwargs)
            log_audit("model_upload_queued", user_id=current_user.id, resource_id=unique_id)
            flash("Model upload accepted. Processing has started in the background.", "success")
            return redirect(url_for("paper_detail", slug=slug))
            if source_format == "glb":
                glb_errors = validate_glb_file(stl_path)
                if glb_errors:
                    flash("Invalid GLB file: " + "; ".join(glb_errors), "danger")
                    cleanup_dir(upload_dir)
                    cleanup_dir(converted_dir)
                    return redirect(url_for("paper_detail", slug=slug))
                os.replace(stl_path, glb_path)
            else:
                stl_errors = validate_stl_file(stl_path)
                if stl_errors:
                    flash("Invalid STL file: " + "; ".join(stl_errors), "danger")
                    cleanup_dir(upload_dir)
                    cleanup_dir(converted_dir)
                    return redirect(url_for("paper_detail", slug=slug))

                converter = STLConverter()
                success = converter.convert(
                    stl_path, glb_path, color=color, source_unit=source_unit
                )
                if not success or not os.path.exists(glb_path):
                    flash(
                        "Conversion failed: "
                        + converter_message(converter, "The STL file could not be converted to GLB."),
                        "danger",
                    )
                    cleanup_dir(upload_dir)
                    cleanup_dir(converted_dir)
                    return redirect(url_for("paper_detail", slug=slug))

            # Optional iOS USDZ companion. Priority order:
            #   1. User-uploaded .usdz (most reliable; control over Reality
            #      Composer / pro tooling output quality)
            #   2. Server-side auto-conversion via aspose-3d if installed
            #   3. None (iOS will fall back to model-viewer's online USDZ Maker
            #      which over-smooths normals — a known iOS Quick Look issue)
            usdz_file = request.files.get("usdz")
            if usdz_file and usdz_file.filename and usdz_file.filename.lower().endswith(".usdz"):
                try:
                    usdz_file.save(usdz_path)
                    logger.info("User-provided USDZ stored at %s", usdz_path)
                except OSError as e:
                    logger.warning("Failed to save user USDZ: %s", e)
            elif os.path.exists(glb_path):
                # Try automatic conversion; silently skip if aspose-3d not installed
                convert_glb_to_usdz(glb_path, usdz_path)

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
                source_format=source_format,
                anonymization_confirmed=True,
                rights_confirmed=True,
                ethics_responsibility_confirmed=True,
                consent_confirmed_at=datetime.now(UTC),
                consent_ip=request.headers.get("X-Forwarded-For", request.remote_addr),
                terms_version=app.config.get("TERMS_VERSION", "1.0"),
            )
            db.session.add(model)
            db.session.commit()
            log_audit("model_uploaded", user_id=current_user.id, resource_id=unique_id)
        except SQLAlchemyError:
            db.session.rollback()
            cleanup_dir(upload_dir)
            cleanup_dir(converted_dir)
            cleanup_file(os.path.join(app.config["QR_FOLDER"], f"qr_{unique_id}.png"))
            logger.exception("Model save failed")
            flash("The model could not be saved. Please try again.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        except Exception:
            db.session.rollback()
            cleanup_dir(upload_dir)
            cleanup_dir(converted_dir)
            cleanup_file(os.path.join(app.config["QR_FOLDER"], f"qr_{unique_id}.png"))
            logger.exception("STL to GLB conversion failed")
            flash("Unexpected conversion error. Please check the file and try again.", "danger")
            return redirect(url_for("paper_detail", slug=slug))

        flash("Model uploaded and QR code generated.", "success")
        return redirect(url_for("paper_detail", slug=slug))

    @app.route("/models/<model_id>/status")
    @login_required
    def model_status(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        if model.user_id != current_user.id:
            abort(403)
        return jsonify(
            {
                "id": model.id,
                "status": model.processing_status or "ready",
                "error": model.processing_error,
                "has_qr": bool(model.qr_code_path),
                "viewer_url": url_for("view_model", model_id=model.id),
            }
        )

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
                flash("Model details could not be saved.", "danger")
                return render_template("model_edit.html", model=model, paper=model.paper)
            flash("Model details updated.", "success")
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
            log_audit("model_deleted", user_id=current_user.id, resource_id=model_id)
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Model delete failed")
            flash("The model could not be deleted. Please try again.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        cleanup_paths(file_paths)
        flash("Model deleted.", "info")
        return redirect(url_for("paper_detail", slug=slug))


app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=app.config.get("DEBUG", False),
    )
