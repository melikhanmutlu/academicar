"""AcademicAR Flask application entry point."""
import logging
import os
import re
import secrets
import shutil
import struct
import uuid
from datetime import UTC, datetime, timedelta

from flask import Flask, abort, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_limiter import Limiter
from flask_limiter.errors import RateLimitExceeded
from flask_limiter.util import get_remote_address
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
from converters import FBXConverter, OBJConverter, STLConverter
from converters.stl_converter import convert_glb_to_usdz, enrich_glb_for_ar
from licensing import (
    LICENSE_PLANS,
    apply_model_license_defaults,
    get_license_plan,
    is_access_expired,
    license_expires_at,
    model_access_status,
    model_file_limit_error,
    model_is_accessible,
    normalize_license_type,
)
from licensing import paper_is_expired as licensing_paper_is_expired
from models import AuditLog, ConversionJob, Model3D, ModelVersion, Paper, Payment, QRLink, User, db
from url_helpers import public_url
from utils.security import require_model_ownership, require_paper_ownership
from services.storage_service import StorageError, safe_move_file, safe_save_file, save_companion_files


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
csrf = CSRFProtect()
limiter = Limiter(key_func=lambda: rate_limit_key(), default_limits=[])


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    app_env = str(app.config.get("APP_ENV", "development")).lower()
    if app.config.get("TESTING"):
        app.config["RATELIMIT_STORAGE_URI"] = "memory://"
    elif app_env not in {"production", "prod", "pilot"} and not os.environ.get("RATELIMIT_STORAGE_URI"):
        app.config["RATELIMIT_STORAGE_URI"] = "memory://"
    else:
        app.config["RATELIMIT_STORAGE_URI"] = (
            app.config.get("RATELIMIT_STORAGE_URI")
            or app.config.get("REDIS_URL")
            or "memory://"
        )
    app.config.setdefault("RATELIMIT_HEADERS_ENABLED", True)
    validate_secret_key(app)
    Config.init_app(app)
    # ProxyFix is enabled whenever a reverse proxy is in front of us. In dev
    # the test client and Flask's dev server set remote_addr correctly so this
    # has no effect; in production it lets request.remote_addr reflect the
    # real client (one trusted hop) without trusting raw X-Forwarded-For.
    proxy_hops = int(app.config.get("PROXY_FIX_HOPS", 1 if app.config.get("APP_ENV") in {"production", "prod", "pilot"} else 0))
    if proxy_hops > 0:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=proxy_hops,
            x_proto=proxy_hops,
            x_host=proxy_hops,
            x_port=proxy_hops,
        )

    db.init_app(app)
    Migrate(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
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
            "license_plans": LICENSE_PLANS,
            "get_license_plan": get_license_plan,
            "model_resolver_url": model_resolver_url,
            "model_access_status": model_access_status,
        }

    @app.after_request
    def set_security_headers(response):
        # Lightweight set of always-on hardening headers. Full CSP is opt-in
        # to avoid breaking the model-viewer / Tailwind CDN pages.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if app.config.get("APP_ENV") in {"production", "prod", "pilot"}:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response

    register_error_handlers(app)
    register_routes(app)

    with app.app_context():
        try:
            db.create_all()
        except OperationalError as exc:
            if "already exists" not in str(exc).lower():
                raise
            logger.warning("SQLite schema already existed during create_all; continuing with compatibility checks.")
        pass # ensure_sqlite_schema removed

    return app


def allowed_stl(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "stl"


def allowed_glb(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "glb"


SUPPORTED_MODEL_EXTENSIONS = {"stl", "glb", "obj", "fbx"}
COMPANION_FILE_EXTENSIONS = {".mtl", ".png", ".jpg", ".jpeg", ".webp"}
APPEARANCE_BACKUP_SUFFIX = ".appearance_backup"
ERROR_MESSAGE_MAX_LENGTH = 2000
COLOR_COMMAND_PATTERN = re.compile(
    r"\b(black|white|red|green|blue|yellow|orange|purple|pink|brown|cyan|magenta|gray|grey|"
    r"silver|gold|navy|teal|olive|maroon|lime|aqua|indigo)\b",
    re.IGNORECASE,
)
LIGHT_DARK_PATTERN = re.compile(r"\b(very\s+)?(light|dark)\b", re.IGNORECASE)
HEX_COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}\b")
NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#cc0000", "green": "#0a7a3a",
    "blue": "#1e44ad", "yellow": "#f5c61b", "orange": "#e07b14", "purple": "#7a3fa9",
    "pink": "#e8689b", "brown": "#7a4a23", "cyan": "#16b3c2", "magenta": "#c4239b",
    "gray": "#7a7a7a", "grey": "#7a7a7a", "silver": "#bfbfbf", "gold": "#d6a324",
    "navy": "#001f4d", "teal": "#0d8a83", "olive": "#7a7a14", "maroon": "#5e0f0f",
    "lime": "#86c41e", "aqua": "#16d4d4", "indigo": "#3a1aa1",
}


def allowed_model(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in SUPPORTED_MODEL_EXTENSIONS


def allowed_pdf(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "pdf"


def client_ip() -> str | None:
    """Best-effort client IP for audit / consent records.

    When ProxyFix is active (production / pilot), ``request.remote_addr``
    already reflects the trusted proxy chain. Outside that we fall through to
    remote_addr to avoid trusting client-supplied X-Forwarded-For headers.
    """
    if not request:
        return None
    return request.remote_addr


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


# ensure_sqlite_schema removed


def rate_limit_key() -> str:
    if current_user.is_authenticated:
        return f"user:{current_user.id}"
    return f"ip:{get_remote_address()}"


def upload_rate_limit_value() -> str:
    limit = int(current_app.config.get("UPLOAD_RATE_LIMIT_COUNT", Config.UPLOAD_RATE_LIMIT_COUNT))
    window = int(current_app.config.get("UPLOAD_RATE_LIMIT_WINDOW", Config.UPLOAD_RATE_LIMIT_WINDOW))
    return f"{max(limit, 1)} per {max(window, 1)} seconds"


def upload_rate_limit_disabled() -> bool:
    limit = int(current_app.config.get("UPLOAD_RATE_LIMIT_COUNT", Config.UPLOAD_RATE_LIMIT_COUNT))
    window = int(current_app.config.get("UPLOAD_RATE_LIMIT_WINDOW", Config.UPLOAD_RATE_LIMIT_WINDOW))
    return limit <= 0 or window <= 0


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


def package_expires_at(package_type: str) -> datetime | None:
    if package_type == "academic":
        return datetime.now(UTC) + timedelta(days=365 * 3)
    if package_type == "model_based":
        return None
    return datetime.now(UTC) + timedelta(days=3)


def sync_paper_entitlements(paper: Paper) -> None:
    """Keep package lifetime and payment state enforceable in the database."""
    if paper.package_type == "model_based":
        paper.payment_status = paper.payment_status or "model_based"
        paper.expires_at = None
    elif paper.package_type == "academic":
        paper.payment_status = paper.payment_status or "paid"
        if not paper.expires_at:
            paper.expires_at = package_expires_at("academic")
    else:
        paper.package_type = "temporary"
        paper.payment_status = "free"
        if not paper.expires_at:
            paper.expires_at = package_expires_at("temporary")


def paper_is_expired(paper: Paper) -> bool:
    return licensing_paper_is_expired(paper)


def new_public_id() -> str:
    """Cryptographically random URL-safe public id for QR resolver targets."""
    return secrets.token_urlsafe(16)[:32]


def model_resolver_url(model: Model3D) -> str:
    """Stable public URL the QR code encodes. Falls back to /view/<id> for
    legacy rows that do not yet have a public_id."""
    if not model:
        return ""
    if model.public_id:
        return public_url("model_resolver", public_id=model.public_id)
    return public_url("view_model", model_id=model.id)


def ensure_model_qr_link(model: Model3D) -> QRLink:
    """Ensure the model has a stable public_id and an active QRLink record.

    Idempotent: callable many times for the same model. The QRLink survives
    license upgrades, replacements, and color updates so QR codes never break.
    """
    if not model.public_id:
        public_id = new_public_id()
        # Defensive: ensure global uniqueness across both legacy Model3D and QRLink rows.
        while (
            QRLink.query.filter_by(public_id=public_id).first()
            or Model3D.query.filter_by(public_id=public_id).first()
        ):
            public_id = new_public_id()
        model.public_id = public_id
    qr_link = QRLink.query.filter_by(model_id=model.id, target_type="model_viewer").first()
    if qr_link is None:
        qr_link = QRLink(
            public_id=model.public_id,
            model_id=model.id,
            status="active",
            target_type="model_viewer",
        )
        db.session.add(qr_link)
    elif qr_link.public_id != model.public_id:
        qr_link.public_id = model.public_id
    if qr_link.status != "active":
        qr_link.status = "active"
    return qr_link


def generate_model_qr(model: Model3D, qr_folder: str) -> str:
    """Render the QR code that encodes the managed resolver URL.

    Returns the QR filename (relative to qr_folder)."""
    import qrcode

    target_url = model_resolver_url(model)
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    filename = f"qr_{model.id}.png"
    os.makedirs(qr_folder, exist_ok=True)
    img.save(os.path.join(qr_folder, filename))
    return filename


def archive_source_file(model: Model3D, source_path: str, version: int, app: Flask) -> str:
    """Move a model source file (and any companion files in its directory)
    into a versioned archive directory under UPLOAD_FOLDER.

    Returns the archived path of the primary source file.
    """
    archive_root = os.path.join(app.config["UPLOAD_FOLDER"], model.id, f"v{version}")
    os.makedirs(archive_root, exist_ok=True)
    src_dir = os.path.dirname(source_path)
    dest_path = os.path.join(archive_root, os.path.basename(source_path))
    shutil.copy2(source_path, dest_path)
    if src_dir and os.path.isdir(src_dir):
        for entry in os.listdir(src_dir):
            full = os.path.join(src_dir, entry)
            if not os.path.isfile(full) or full == source_path:
                continue
            ext = os.path.splitext(entry)[1].lower()
            if ext in COMPANION_FILE_EXTENSIONS:
                shutil.copy2(full, os.path.join(archive_root, entry))
    return dest_path


_NAMED_SHADES = {
    # Hand-tuned to match common UI grayscale ramps. "light gray" → #d9d9d9
    # is the canonical web "light gray" (CSS's named "lightgray" is #d3d3d3,
    # we round to a slightly cleaner value so AR captures stay neutral).
    ("light", "gray"): "#d9d9d9",
    ("light", "grey"): "#d9d9d9",
    ("dark", "gray"): "#4a4a4a",
    ("dark", "grey"): "#4a4a4a",
    ("very light", "gray"): "#ededed",
    ("very light", "grey"): "#ededed",
    ("very dark", "gray"): "#242424",
    ("very dark", "grey"): "#242424",
}


def color_from_command(command: str | None) -> str | None:
    """Parse a free-form color command like "make it light gray" -> "#d9d9d9".

    Returns a 7-char hex string on success, None otherwise.
    """
    if not command:
        return None
    text_in = command.strip().lower()
    if not text_in:
        return None
    # Direct hex passthrough.
    hex_match = HEX_COLOR_PATTERN.search(command)
    if hex_match:
        return hex_match.group(0).lower()
    color_match = COLOR_COMMAND_PATTERN.search(text_in)
    if not color_match:
        return None
    base_name = color_match.group(0).lower()
    base = NAMED_COLORS.get(base_name)
    if not base:
        return None
    mod_match = LIGHT_DARK_PATTERN.search(text_in)
    if not mod_match:
        return base
    very = bool(mod_match.group(1))
    direction = mod_match.group(2).lower()
    modifier = ("very " if very else "") + direction
    named = _NAMED_SHADES.get((modifier, base_name))
    if named:
        return named
    return _shift_color(base, lighten=(direction == "light"), strong=very)


def _shift_color(hex_color: str, *, lighten: bool, strong: bool) -> str:
    """Lighten or darken a hex color by a fixed amount."""
    factor = 0.7 if strong else 0.5
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except (ValueError, IndexError):
        return hex_color
    if lighten:
        r = int(r + (255 - r) * factor)
        g = int(g + (255 - g) * factor)
        b = int(b + (255 - b) * factor)
    else:
        r = int(r * (1 - factor))
        g = int(g * (1 - factor))
        b = int(b * (1 - factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def hex_to_rgba(hex_color: str | None) -> tuple[float, float, float, float] | None:
    """Convert "#RRGGBB" to a normalized RGBA tuple. Returns None for invalid input."""
    if not hex_color or not isinstance(hex_color, str):
        return None
    color = hex_color.strip()
    if not color.startswith("#") or len(color) != 7:
        return None
    try:
        r = int(color[1:3], 16) / 255.0
        g = int(color[3:5], 16) / 255.0
        b = int(color[5:7], 16) / 255.0
    except ValueError:
        return None
    return (r, g, b, 1.0)


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


def mark_model_failed(
    model_id: str,
    message: str,
    *,
    is_replacement: bool = False,
    job: ConversionJob | None = None,
    version: ModelVersion | None = None,
) -> None:
    model = db.session.get(Model3D, model_id)
    if not model:
        return
    truncated = (message or "")[:ERROR_MESSAGE_MAX_LENGTH]
    if is_replacement:
        # Preserve the previous working processing_status (e.g. "ready") so
        # the public viewer keeps serving the old GLB. Surface the failure
        # via the dedicated replacement_* fields plus a marker status.
        model.processing_status = "replacement_failed"
        model.replacement_status = "replacement_failed"
        model.replacement_error = truncated
    else:
        model.processing_status = "failed"
        model.processing_error = truncated
    if job is not None:
        job.status = "failed"
        job.error = truncated
        job.finished_at = datetime.now(UTC)
    if version is not None:
        version.status = "failed"
        version.error = truncated
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()


def _get_converter_for_format(source_format: str):
    """Return a fresh converter instance for the given source format, or None."""
    fmt = (source_format or "").lower()
    if fmt == "stl":
        return STLConverter()
    if fmt == "obj":
        return OBJConverter()
    if fmt == "fbx":
        return FBXConverter()
    return None


def _run_converter(converter, source_path: str, glb_path: str, *, color: str | None, source_unit: str) -> bool:
    """Call converter.convert with backwards-compatible kwargs."""
    try:
        return converter.convert(source_path, glb_path, color=color, source_unit=source_unit)
    except TypeError:
        return converter.convert(source_path, glb_path, color=color)


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
    is_replacement: bool = False,
    job_id: int | None = None,
    version_id: int | None = None,
) -> None:
    """Convert/copy the uploaded model and update Model3D / ConversionJob /
    ModelVersion rows. Runs synchronously in tests, or in the isolated worker
    process via the DB-backed queue.

    Atomicity: replacement writes go to a sibling ".new" file first and are
    swapped in only after a successful conversion, so the previous working
    GLB is preserved on failure.
    """
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        if not model:
            cleanup_dir(upload_dir)
            cleanup_dir(converted_dir)
            return

        job = db.session.get(ConversionJob, job_id) if job_id is not None else None
        version = db.session.get(ModelVersion, version_id) if version_id is not None else None
        if job is not None:
            job.status = "processing"
            job.started_at = datetime.now(UTC)
            job.attempts = (job.attempts or 0) + 1
        if version is not None:
            version.status = "processing"
        if not is_replacement:
            model.processing_status = "processing"
            model.processing_error = None
        else:
            model.replacement_status = "replacement_processing"
            model.replacement_error = None
        db.session.commit()

        # For replacements we write to a sibling "<basename>.new.glb" path
        # (keeping the .glb extension so trimesh and friends pick the right
        # exporter) and only os.replace() it onto glb_path on success. This
        # guarantees that a failed conversion never corrupts the previously
        # working GLB.
        if is_replacement:
            base, ext = os.path.splitext(glb_path)
            target_glb = f"{base}.new{ext}"
        else:
            target_glb = glb_path

        try:
            converter = None
            if source_format == "glb":
                glb_errors = validate_glb_file(source_path)
                if glb_errors:
                    cleanup_file(target_glb)
                    mark_model_failed(
                        model_id,
                        "Invalid GLB file: " + "; ".join(glb_errors),
                        is_replacement=is_replacement,
                        job=job,
                        version=version,
                    )
                    cleanup_dir(upload_dir)
                    return
                os.makedirs(os.path.dirname(target_glb), exist_ok=True)
                shutil.copy2(source_path, target_glb)
                if color:
                    rgba = hex_to_rgba(color)
                    if rgba is not None:
                        try:
                            enrich_glb_for_ar(target_glb, rgba)
                        except Exception:
                            logger.exception("enrich_glb_for_ar failed for direct GLB upload")
            elif source_format == "stl":
                stl_errors = validate_stl_file(source_path)
                if stl_errors:
                    cleanup_file(target_glb)
                    mark_model_failed(
                        model_id,
                        "Invalid STL file: " + "; ".join(stl_errors),
                        is_replacement=is_replacement,
                        job=job,
                        version=version,
                    )
                    cleanup_dir(upload_dir)
                    return
                converter = STLConverter()
                success = _run_converter(
                    converter, source_path, target_glb, color=color, source_unit=source_unit
                )
                if not success or not os.path.exists(target_glb):
                    cleanup_file(target_glb)
                    mark_model_failed(
                        model_id,
                        "Conversion failed: "
                        + converter_message(converter, "The file could not be converted to GLB."),
                        is_replacement=is_replacement,
                        job=job,
                        version=version,
                    )
                    cleanup_dir(upload_dir)
                    return
            else:
                converter = _get_converter_for_format(source_format)
                if converter is None:
                    mark_model_failed(
                        model_id,
                        f"Unsupported source format: {source_format}",
                        is_replacement=is_replacement,
                        job=job,
                        version=version,
                    )
                    cleanup_dir(upload_dir)
                    return
                success = _run_converter(
                    converter, source_path, target_glb, color=color, source_unit=source_unit
                )
                if not success or not os.path.exists(target_glb):
                    cleanup_file(target_glb)
                    mark_model_failed(
                        model_id,
                        "Conversion failed: "
                        + converter_message(converter, "The file could not be converted to GLB."),
                        is_replacement=is_replacement,
                        job=job,
                        version=version,
                    )
                    cleanup_dir(upload_dir)
                    return

            # Atomic swap for replacements: the previous working GLB is only
            # overwritten when the new GLB is fully on disk.
            if is_replacement and target_glb != glb_path:
                os.replace(target_glb, glb_path)

            if os.path.exists(glb_path) and not os.path.exists(usdz_path):
                try:
                    convert_glb_to_usdz(glb_path, usdz_path)
                except Exception:  # USDZ companion is best-effort.
                    logger.exception("USDZ generation failed; continuing without iOS companion.")

            model = db.session.get(Model3D, model_id)
            if not model:
                return

            ensure_model_qr_link(model)
            qr_filename = generate_model_qr(model, app.config["QR_FOLDER"])
            model.qr_code_path = qr_filename
            model.file_size = os.path.getsize(glb_path)
            model.processing_status = "ready"
            model.processing_error = None
            if color:
                model.appearance_color = color
            if is_replacement:
                model.replacement_status = "ready"
                model.replacement_error = None
            apply_model_license_defaults(model, model.license_type)

            if job is not None:
                job.status = "completed"
                job.error = None
                job.finished_at = datetime.now(UTC)
            if version is not None:
                version.status = "ready"
                version.glb_path = glb_path
                version.file_size = model.file_size
                version.material_color = color
                version.error = None

            db.session.add(
                AuditLog(
                    event_type="model_replaced" if is_replacement else "model_processed",
                    user_id=model.user_id,
                    resource_id=model_id,
                    details={"source_format": source_format},
                )
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Background model processing failed")
            cleanup_file(target_glb if is_replacement else None)
            mark_model_failed(
                model_id,
                "Unexpected conversion error. Please check the file and try again.",
                is_replacement=is_replacement,
                job=job,
                version=version,
            )
            cleanup_dir(upload_dir)


def enqueue_conversion_job(
    app: Flask,
    *,
    model: Model3D,
    job_kwargs: dict,
    job_type: str = "model_upload",
) -> ConversionJob:
    """Persist a ConversionJob row for the isolated worker service.

    Tests and explicit local development runs may opt into inline execution,
    but production web processes only enqueue work and return.
    """
    job = ConversionJob(
        job_type=job_type,
        status="pending",
        model_id=model.id,
        user_id=model.user_id,
        payload=dict(job_kwargs),
    )
    db.session.add(job)
    db.session.commit()
    job_kwargs = dict(job_kwargs)
    job_kwargs["job_id"] = job.id
    if app.config.get("TESTING") or app.config.get("DEV_INLINE_JOBS"):
        process_model_upload_job(app, **job_kwargs)
    return job


def run_next_conversion_job(app: Flask) -> bool:
    """Pick up the oldest pending ConversionJob and run it.

    Used by ``worker.py`` for the Railway worker service. Returns True when a
    job was processed (or attempted), so the caller can immediately poll for
    the next job rather than sleeping.
    """
    with app.app_context():
        job = (
            ConversionJob.query
            .filter(ConversionJob.status == "pending")
            .order_by(ConversionJob.created_at.asc())
            .first()
        )
        if job is None:
            return False
        # Claim the job atomically. We rely on the queue being read by a single
        # worker for the MVP; multi-worker deployments should add SELECT FOR UPDATE.
        job.status = "processing"
        job.started_at = datetime.now(UTC)
        job.attempts = (job.attempts or 0) + 1
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return False
        payload = dict(job.payload or {})
        payload["job_id"] = job.id
    process_model_upload_job(app, **payload)
    return True


def _create_model_for_paper(
    paper: Paper,
    file,
    companion_files: list,
    *,
    license_type: str | None,
    display_name: str | None,
    description: str | None,
    color: str | None,
    source_unit: str | None,
    compliance_confirm: str | None,
) -> tuple[bool, str]:
    """Shared model upload pipeline used by paper_new (first-model) and the
    /papers/<slug>/upload-model endpoint.

    Returns (ok, flash_message). Always commits a Model3D + ModelVersion +
    ConversionJob trio. Conversion runs synchronously only in tests/local
    DEV_INLINE_JOBS; production workers pick up the DB-backed job.
    """
    from flask import current_app

    if not allowed_model(file.filename):
        return False, "Only .stl, .glb, .obj, or .fbx files are accepted."
    if compliance_confirm != "yes":
        return False, (
            "You must confirm that the model is anonymized and that you have "
            "the right to share it."
        )
    license_normalized = normalize_license_type(license_type)
    display_name = (display_name or "").strip() or None
    description = (description or "").strip() or None
    color = (color or "").strip() or None
    if color and HEX_COLOR_PATTERN.fullmatch(color) is None:
        color = None
    source_unit_norm = (source_unit or "auto").strip().lower()
    if source_unit_norm not in {"auto", "mm", "cm", "m"}:
        source_unit_norm = "auto"

    unique_id = str(uuid.uuid4())
    original_name = secure_filename(file.filename)
    source_format = original_name.rsplit(".", 1)[1].lower()

    upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_id)
    converted_dir = os.path.join(current_app.config["CONVERTED_FOLDER"], unique_id)
    os.makedirs(upload_dir, exist_ok=True)
    os.makedirs(converted_dir, exist_ok=True)
    source_path = os.path.join(upload_dir, original_name)
    glb_path = os.path.join(converted_dir, "model.glb")
    usdz_path = os.path.join(converted_dir, "model.usdz")

    try:
        safe_save_file(file, source_path)
    except StorageError as e:
        cleanup_dir(upload_dir)
        cleanup_dir(converted_dir)
        return False, str(e)

    if source_format == "obj" and companion_files:
        try:
            save_companion_files(companion_files, upload_dir, COMPANION_FILE_EXTENSIONS)
        except StorageError as e:
            cleanup_dir(upload_dir)
            cleanup_dir(converted_dir)
            return False, str(e)

    file_size = os.path.getsize(source_path)
    size_error = model_file_limit_error(file_size, license_normalized)
    if size_error:
        cleanup_dir(upload_dir)
        cleanup_dir(converted_dir)
        return False, size_error

    # Cheap preflight on the formats we can introspect without external tools.
    if source_format == "glb":
        preflight_errors = validate_glb_file(source_path)
    elif source_format == "stl":
        preflight_errors = validate_stl_file(source_path)
    else:
        preflight_errors = []
    if preflight_errors:
        cleanup_dir(upload_dir)
        cleanup_dir(converted_dir)
        return False, f"Invalid {source_format.upper()} file: " + "; ".join(preflight_errors)

    # Archive the originals into a versioned directory so conversions can be
    # rerun and replacements can be audited later.
    archive_root = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_id, "v1")
    os.makedirs(archive_root, exist_ok=True)
    archived_source = os.path.join(archive_root, original_name)
    shutil.copy2(source_path, archived_source)
    if source_format == "obj":
        for entry in os.listdir(upload_dir):
            full = os.path.join(upload_dir, entry)
            if not os.path.isfile(full) or full == source_path:
                continue
            ext = os.path.splitext(entry)[1].lower()
            if ext in COMPANION_FILE_EXTENSIONS:
                shutil.copy2(full, os.path.join(archive_root, entry))

    model = Model3D(
        id=unique_id,
        paper_id=paper.id,
        user_id=paper.user_id,
        display_name=display_name,
        description=description,
        original_filename=original_name,
        original_source_path=archived_source,
        current_source_path=archived_source,
        glb_path=glb_path,
        storage_provider=current_app.config.get("STORAGE_PROVIDER", "railway_volume"),
        storage_key=os.path.relpath(glb_path, current_app.config["CONVERTED_FOLDER"]).replace("\\", "/"),
        qr_code_path=None,
        file_size=file_size,
        source_format=source_format,
        appearance_color=color,
        version=1,
        processing_status="queued",
        anonymization_confirmed=True,
        rights_confirmed=True,
        ethics_responsibility_confirmed=True,
        consent_confirmed_at=datetime.now(UTC),
        consent_ip=client_ip(),
        terms_version=current_app.config.get("TERMS_VERSION", "1.0"),
    )
    apply_model_license_defaults(model, license_normalized)
    db.session.add(model)
    # Public id + QR record exist from the moment the model is created so QR
    # codes can be printed even before conversion completes.
    ensure_model_qr_link(model)
    version_row = ModelVersion(
        model_id=unique_id,
        version_number=1,
        source_path=archived_source,
        glb_path=glb_path,
        source_format=source_format,
        file_size=file_size,
        material_color=color,
        storage_provider=model.storage_provider,
        storage_key=model.storage_key,
        status="queued",
    )
    db.session.add(version_row)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        cleanup_dir(upload_dir)
        cleanup_dir(converted_dir)
        logger.exception("Could not persist Model3D / ModelVersion rows")
        return False, "The model could not be saved. Please try again."

    job_kwargs = {
        "model_id": unique_id,
        "upload_dir": upload_dir,
        "converted_dir": converted_dir,
        "source_path": archived_source,
        "glb_path": glb_path,
        "usdz_path": usdz_path,
        "source_format": source_format,
        "color": color,
        "source_unit": source_unit_norm,
        "version_id": version_row.id,
    }
    enqueue_conversion_job(current_app, model=model, job_kwargs=job_kwargs, job_type="model_upload")
    log_audit("model_upload_queued", user_id=paper.user_id, resource_id=unique_id)
    return True, "Model upload accepted. Processing has started in the background."


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

    @app.errorhandler(RateLimitExceeded)
    def rate_limit_exceeded(error):
        flash("Too many upload attempts in a short time. Please try again in a few minutes.", "warning")
        referrer = request.referrer or url_for("dashboard")
        return redirect(referrer)

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
    """Log an audit event for privacy and compliance tracking."""
    try:
        ip_address = client_ip()
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

    @app.route("/data-protection")
    def data_protection():
        return render_template("legal/data_protection.html")

    @app.route("/view/<model_id>")
    def view_model(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        status = model_access_status(model)
        if status != "active":
            return (
                render_template(
                    "model_access_unavailable.html",
                    model=model,
                    paper=model.paper,
                    status=status,
                ),
                410,
            )
        usdz_path = os.path.join(
            app.config["CONVERTED_FOLDER"], model.id, "model.usdz"
        )
        has_usdz = os.path.exists(usdz_path)
        return render_template(
            "viewer.html", model=model, paper=model.paper, has_usdz=has_usdz
        )

    @app.route("/m/<public_id>")
    def model_resolver(public_id):
        """Managed QR resolver: stable public URL that survives storage and
        license changes. Always returns one of: 302 redirect to viewer, 410
        unavailable page, or 404."""
        qr_link = QRLink.query.filter_by(public_id=public_id).first()
        model = qr_link.model if qr_link else Model3D.query.filter_by(public_id=public_id).first()
        if not model:
            abort(404)
        if qr_link is not None:
            qr_link.last_resolved_at = datetime.now(UTC)
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
        status = model_access_status(model)
        if status != "active":
            return (
                render_template(
                    "model_access_unavailable.html",
                    model=model,
                    paper=model.paper,
                    status=status,
                ),
                410,
            )
        return redirect(url_for("view_model", model_id=model.id))

    @app.route("/files/<unique_id>/<path:filename>")
    def serve_glb(unique_id, filename):
        if not is_uuid(unique_id) or filename not in {"model.glb", "model.usdz"}:
            abort(404)
        model = db.session.get(Model3D, unique_id)
        if not model:
            abort(404)
        if not model_is_accessible(model):
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

    @app.route("/admin")
    @login_required
    def admin_dashboard():
        if not current_user.is_admin:
            abort(403)
        users = User.query.order_by(User.created_at.desc()).limit(100).all()
        papers = Paper.query.order_by(Paper.created_at.desc()).limit(100).all()
        models = Model3D.query.order_by(Model3D.created_at.desc()).limit(100).all()
        payments = Payment.query.order_by(Payment.created_at.desc()).limit(50).all()
        qr_links = QRLink.query.order_by(QRLink.created_at.desc()).limit(100).all()
        return render_template(
            "admin_dashboard.html",
            users=users,
            papers=papers,
            models=models,
            payments=payments,
            qr_links=qr_links,
        )

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
        min_length = app.config.get("PASSWORD_MIN_LENGTH", 8)
        if len(new_pw) < min_length:
            flash(f"New password must be at least {min_length} characters.", "danger")
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

    @app.route("/papers/fetch-metadata", methods=["POST"])
    @login_required
    def papers_fetch_metadata():
        """API endpoint to fetch paper metadata by DOI or PMID using public APIs."""
        import urllib.request
        import urllib.parse
        import json
        import re

        data = request.get_json() or {}
        query = (data.get("query") or "").strip()

        if not query:
            return jsonify({"success": False, "error": "Query cannot be empty"}), 400

        is_pmid = query.isdigit()
        is_doi = query.startswith("10.") or "doi.org/" in query.lower()

        if not is_pmid and not is_doi:
            if re.search(r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b', query, re.IGNORECASE):
                is_doi = True
            else:
                return jsonify({"success": False, "error": "Please enter a valid DOI (e.g. 10.1148/radiol.210408) or PMID"}), 400

        if is_doi:
            doi = query
            doi = re.sub(r'^(https?://)?(dx\.)?doi\.org/', '', doi, flags=re.IGNORECASE)
            url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
            headers = {"User-Agent": "AcademicAR/1.0 (mailto:admin@academicar.com)"}
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status == 200:
                        res_data = json.loads(response.read().decode('utf-8'))
                        message = res_data.get("message", {})
                        
                        titles = message.get("title", [])
                        title = titles[0] if titles else ""
                        
                        author_list = []
                        for author in message.get("author", []):
                            given = author.get("given", "")
                            family = author.get("family", "")
                            if given and family:
                                author_list.append(f"{family} {given}")
                            elif family:
                                author_list.append(family)
                        authors = ", ".join(author_list)
                        
                        year = None
                        for date_field in ["published-print", "published-online", "created"]:
                            date_parts = message.get(date_field, {}).get("date-parts", [])
                            if date_parts and date_parts[0]:
                                year = date_parts[0][0]
                                break
                        
                        abstract = message.get("abstract", "")
                        abstract = re.sub(r'<[^>]+>', '', abstract)
                        abstract = re.sub(r'\s+', ' ', abstract).strip()
                        
                        publisher = message.get("publisher", "")
                        container = message.get("container-title", [])
                        journal = container[0] if container else publisher
                        
                        return jsonify({
                            "success": True,
                            "title": title,
                            "authors": authors,
                            "year": year,
                            "abstract": abstract,
                            "institution": journal,
                            "doi": doi,
                            "pmid": ""
                        })
            except Exception as e:
                logger.error(f"Error fetching DOI {doi}: {e}")
                return jsonify({"success": False, "error": "DOI not found or service is currently unavailable"}), 404

        elif is_pmid:
            url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={query}&retmode=json"
            req = urllib.request.Request(url, headers={"User-Agent": "AcademicAR/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status == 200:
                        res_data = json.loads(response.read().decode('utf-8'))
                        result = res_data.get("result", {})
                        uid_data = result.get(query, {})
                        
                        if "error" in uid_data or not uid_data.get("title"):
                            return jsonify({"success": False, "error": f"PMID {query} not found"}), 404
                        
                        title = uid_data.get("title", "")
                        
                        author_list = []
                        for author in uid_data.get("authors", []):
                            name = author.get("name", "")
                            if name:
                                author_list.append(name)
                        authors = ", ".join(author_list)
                        
                        pubdate = uid_data.get("pubdate", "")
                        year_match = re.search(r'\b(19|20)\d{2}\b', pubdate)
                        year = int(year_match.group(0)) if year_match else None
                        
                        journal = uid_data.get("source", "")
                        
                        doi = ""
                        for articleid in uid_data.get("articleids", []):
                            if articleid.get("idtype") == "doi":
                                doi = articleid.get("value", "")
                                break
                                
                        return jsonify({
                            "success": True,
                            "title": title,
                            "authors": authors,
                            "year": year,
                            "abstract": "",
                            "institution": journal,
                            "doi": doi,
                            "pmid": query
                        })
            except Exception as e:
                logger.error(f"Error fetching PMID {query}: {e}")
                return jsonify({"success": False, "error": "PMID not found or service is currently unavailable"}), 404

        return jsonify({"success": False, "error": "Could not parse query"}), 400

    @app.route("/papers/new", methods=["GET", "POST"])
    @login_required
    @limiter.limit(
        upload_rate_limit_value,
        methods=["POST"],
        exempt_when=lambda: upload_rate_limit_disabled()
        or not (
            request.files.get("model_file")
            and request.files.get("model_file").filename
        ),
    )
    def paper_new():
        if request.method == "POST":
            paper_data, paper_errors = validate_paper_form(request.form)
            if paper_errors:
                flash(" ".join(paper_errors), "danger")
                return render_template("paper_new.html", form=request.form)

            # Plan is now a user-level setting (managed on /profile). New
            # papers inherit the user's current plan as a snapshot — this is
            # what determines expiration and multi-model upload limits.
            paper = Paper(
                title=paper_data["title"],
                authors=paper_data["authors"],
                year=paper_data["year"],
                field=paper_data["field"],
                abstract=paper_data["abstract"],
                doi=paper_data["doi"],
                institution=paper_data["institution"],
                pmid=paper_data["pmid"],
                package_type="model_based",
                is_public=paper_data["is_public"],
                payment_status="model_based",
                expires_at=None,
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
                try:
                    safe_save_file(pdf_file, saved_pdf_path)
                except StorageError as e:
                    flash(str(e), "danger")
                    return render_template("paper_new.html", form=request.form)
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

            # MVP §11 / §8: optional first model upload during paper creation.
            # The model upload must NOT roll back the paper on failure — the
            # user can retry from the paper detail page.
            first_model_file = request.files.get("model_file") or request.files.get("model")
            if first_model_file and first_model_file.filename:
                ok, message = _create_model_for_paper(
                    paper,
                    first_model_file,
                    request.files.getlist("model_companion_files"),
                    license_type=request.form.get("license_type"),
                    display_name=request.form.get("model_display_name"),
                    description=request.form.get("model_description"),
                    color=request.form.get("color"),
                    source_unit=request.form.get("source_unit"),
                    compliance_confirm=request.form.get("compliance_confirm"),
                )
                category = "success" if ok else "danger"
                flash(message, category)
            else:
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
    @require_paper_ownership
    def paper_edit(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()

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
                try:
                    safe_save_file(pdf_file, saved_pdf_path)
                except StorageError as e:
                    flash(str(e), "danger")
                    return render_template("paper_new.html", form=request.form, paper=paper, mode="edit")
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
    @require_paper_ownership
    def paper_delete(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
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
    @limiter.limit(upload_rate_limit_value, methods=["POST"], exempt_when=upload_rate_limit_disabled)
    @require_paper_ownership
    def upload_model(slug):
        paper = Paper.query.filter_by(slug=slug).first_or_404()
        file = request.files.get("file") or request.files.get("model_file")
        if not file or not file.filename:
            flash("No file selected.", "danger")
            return redirect(url_for("paper_detail", slug=slug))

        ok, message = _create_model_for_paper(
            paper,
            file,
            request.files.getlist("companion_files"),
            license_type=request.form.get("license_type"),
            display_name=request.form.get("display_name"),
            description=request.form.get("description"),
            color=request.form.get("color"),
            source_unit=request.form.get("source_unit"),
            compliance_confirm=request.form.get("compliance_confirm"),
        )
        flash(message, "success" if ok else "danger")
        return redirect(url_for("paper_detail", slug=slug))

    @app.route("/models/<model_id>/license", methods=["POST"])
    @login_required
    @require_model_ownership
    def model_license_update(model_id):
        """Upgrade or change a model's license tier without breaking its
        public_id, QR code, or resolver URL."""
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        new_license = normalize_license_type(request.form.get("license_type"))
        previous = model.license_type or "free"
        try:
            apply_model_license_defaults(model, new_license)
            db.session.commit()
            log_audit(
                "model_license_changed",
                user_id=current_user.id,
                resource_id=model_id,
                details={"from": previous, "to": new_license},
            )
            flash(f"License updated to {get_license_plan(new_license).label}.", "success")
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("License upgrade failed")
            flash("Could not update the model license. Please try again.", "danger")
        return redirect(url_for("paper_detail", slug=model.paper.slug))

    @app.route("/models/<model_id>/replace", methods=["POST"])
    @login_required
    @limiter.limit(upload_rate_limit_value, methods=["POST"], exempt_when=upload_rate_limit_disabled)
    @require_model_ownership
    def model_replace(model_id):
        """Replace the model's source file while preserving its model_id,
        public_id, QR code, and resolver URL. The previous working GLB is
        kept on disk until the new conversion succeeds (atomic swap)."""
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        file = request.files.get("file") or request.files.get("model_file")
        if not file or not file.filename:
            flash("No replacement file selected.", "danger")
            return redirect(url_for("paper_detail", slug=model.paper.slug))
        if not allowed_model(file.filename):
            flash("Replacement must be a .stl, .glb, .obj, or .fbx file.", "danger")
            return redirect(url_for("paper_detail", slug=model.paper.slug))
        if request.form.get("compliance_confirm") != "yes":
            flash(
                "You must reconfirm anonymization, rights, and ethics responsibility before replacing the model.",
                "danger",
            )
            return redirect(url_for("paper_detail", slug=model.paper.slug))

        original_name = secure_filename(file.filename)
        source_format = original_name.rsplit(".", 1)[1].lower()
        next_version = (model.version or 1) + 1

        # Stage upload in a temporary scratch dir so the previous source files
        # are kept intact until the new version is committed.
        upload_dir = os.path.join(app.config["UPLOAD_FOLDER"], f"_replace_{uuid.uuid4().hex}")
        os.makedirs(upload_dir, exist_ok=True)
        source_path = os.path.join(upload_dir, original_name)
        try:
            safe_save_file(file, source_path)
        except StorageError as e:
            cleanup_dir(upload_dir)
            flash(str(e), "danger")
            return redirect(url_for("paper_detail", slug=model.paper.slug))

        # OBJ companions live alongside the source so the converter can resolve
        # MTL / texture references from the same directory.
        if source_format == "obj":
            try:
                save_companion_files(
                    request.files.getlist("companion_files"),
                    upload_dir,
                    COMPANION_FILE_EXTENSIONS,
                )
            except StorageError as e:
                cleanup_dir(upload_dir)
                flash(str(e), "danger")
                return redirect(url_for("paper_detail", slug=model.paper.slug))

        size_error = model_file_limit_error(os.path.getsize(source_path), model.license_type)
        if size_error:
            cleanup_dir(upload_dir)
            flash(size_error, "danger")
            return redirect(url_for("paper_detail", slug=model.paper.slug))

        # Archive the new source under uploads/<model_id>/v<n>/ so we have a
        # tamper-evident trail of every replacement attempt.
        archived_source = archive_source_file(model, source_path, next_version, app)

        version_row = ModelVersion(
            model_id=model.id,
            version_number=next_version,
            source_path=archived_source,
            glb_path=model.glb_path,
            source_format=source_format,
            file_size=os.path.getsize(archived_source),
            material_color=model.appearance_color,
            storage_provider=model.storage_provider,
            storage_key=model.storage_key,
            status="queued",
        )
        db.session.add(version_row)
        # Optimistically update bookkeeping that survives a failed replace —
        # original_filename and version are about *which* source we tried, not
        # the currently-active GLB. process_model_upload_job() rolls these
        # back via mark_model_failed() if conversion fails (see test).
        model.original_filename = original_name
        model.original_source_path = archived_source
        model.current_source_path = archived_source
        model.source_format = source_format
        model.version = next_version
        model.last_replaced_at = datetime.now(UTC)
        model.replacement_status = "replacement_processing"
        model.replacement_error = None
        db.session.commit()

        glb_path = model.glb_path
        usdz_path = os.path.join(os.path.dirname(glb_path), "model.usdz")
        job_kwargs = {
            "model_id": model.id,
            "upload_dir": upload_dir,
            "converted_dir": os.path.dirname(glb_path),
            "source_path": archived_source,
            "glb_path": glb_path,
            "usdz_path": usdz_path,
            "source_format": source_format,
            "color": model.appearance_color,
            "source_unit": "auto",
            "is_replacement": True,
            "version_id": version_row.id,
        }
        enqueue_conversion_job(app, model=model, job_kwargs=job_kwargs, job_type="model_replace")

        # Reload after job (synchronous in TESTING) to surface its outcome.
        db.session.refresh(model)
        if model.processing_status == "replacement_failed":
            flash(
                f"Replacement failed: {model.replacement_error or 'conversion error'}. "
                "The previous model is still active.",
                "warning",
            )
        else:
            flash("Model file replaced.", "success")
        cleanup_dir(upload_dir)
        return redirect(url_for("paper_detail", slug=model.paper.slug))

    @app.route("/models/<model_id>/appearance", methods=["POST"])
    @login_required
    @require_model_ownership
    def model_appearance_update(model_id):
        """Update appearance (solid color) while preserving model_id, public_id,
        QR code, resolver URL, and the underlying GLB on failure."""
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        color_command = request.form.get("color_command")
        color_input = (request.form.get("color") or "").strip() or None
        # text command takes precedence so users can type "make it light gray".
        parsed = color_from_command(color_command) if color_command else None
        new_color = parsed or color_input
        if not new_color or HEX_COLOR_PATTERN.fullmatch(new_color) is None:
            flash("Provide a valid hex color (#RRGGBB) or a known color name.", "danger")
            return redirect(url_for("paper_detail", slug=model.paper.slug))

        rgba = hex_to_rgba(new_color)
        if rgba is None:
            flash("Invalid color value.", "danger")
            return redirect(url_for("paper_detail", slug=model.paper.slug))

        glb_path = model.glb_path
        backup_path = glb_path + APPEARANCE_BACKUP_SUFFIX
        try:
            if os.path.exists(glb_path):
                shutil.copy2(glb_path, backup_path)
            try:
                enrich_glb_for_ar(glb_path, rgba)
            except Exception:
                logger.exception("Appearance enrichment failed; restoring backup")
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, glb_path)
                flash("The model appearance could not be updated. The previous version is still active.", "warning")
                return redirect(url_for("paper_detail", slug=model.paper.slug))

            model.appearance_color = new_color
            db.session.commit()
            log_audit(
                "model_appearance_updated",
                user_id=current_user.id,
                resource_id=model_id,
                details={"color": new_color},
            )
            flash(f"Model color updated to {new_color}.", "success")
        except SQLAlchemyError:
            db.session.rollback()
            if os.path.exists(backup_path):
                try:
                    shutil.copy2(backup_path, glb_path)
                except OSError:
                    logger.exception("Failed to restore appearance backup after DB error")
            flash("The model appearance could not be updated.", "danger")
        finally:
            if os.path.exists(backup_path):
                cleanup_file(backup_path)
        return redirect(url_for("paper_detail", slug=model.paper.slug))

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
    @require_model_ownership
    def model_edit(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
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
    @require_model_ownership
    def model_delete(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
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
