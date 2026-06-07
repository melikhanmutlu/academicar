"""AcademicAR Flask application entry point."""
import hashlib
import logging
import os
import re
import secrets
import shutil
import struct
import subprocess
import tempfile
import types
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

from flask import Flask, abort, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from flask_limiter.errors import RateLimitExceeded
from flask_login import LoginManager, current_user, login_required
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFError
from slugify import slugify
from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import selectinload
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from auth import auth_bp, init_oauth
from config import Config
from extensions import csrf, limiter, rate_limit_key
from converters import FBXConverter, OBJConverter, STLConverter
from converters.glb_quality import GLBQualityError, embed_external_textures, ensure_pbr_materials, validate_glb_quality
from converters.glb_optimize import normalize_specular_glossiness, optimize_glb
from converters.poster import generate_poster
from converters.stl_converter import convert_glb_to_usdz, enrich_glb_for_ar
from licensing import (
    LICENSE_PLANS,
    USER_SELECTABLE_PLAN_KEYS,
    apply_model_license_defaults,
    get_license_plan,
    is_access_expired,
    is_valid_user_plan,
    license_expires_at,
    model_access_status,
    model_file_limit_error,
    model_is_accessible,
    normalize_license_type,
)
from licensing import paper_is_expired as licensing_paper_is_expired
from blog_content import code_post_slugs, get_all_posts, get_post, render_body
from discipline_content import all_disciplines, discipline_slugs, get_discipline, related_disciplines
from models import AuditLog, BlogPost, ConversionJob, Model3D, ModelAnnotation, ModelVersion, Paper, Payment, QRLink, User, db
from services.r2_mirror import mirror_file, mirror_directory, mirror_delete, ensure_local
from payments import (
    PAID_PLAN_KEYS,
    apply_successful_payment,
    get_payment_provider,
    plan_amount_minor_units,
)
from url_helpers import public_url
from utils.security import require_model_ownership, require_paper_ownership
from services.storage_service import StorageError, safe_move_file, safe_save_file, save_companion_files


_log_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(level=_log_level, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Baseline Content-Security-Policy. Whitelists the CDNs the app currently
# depends on (Tailwind play CDN, Google model-viewer on unpkg/ajax.googleapis,
# Google Fonts). 'unsafe-eval' is required by the Tailwind play CDN's JIT; both
# it and the inline <script>/<style> blocks need 'unsafe-inline'. Tightening
# these (self-hosted Tailwind build, nonce-based scripts) is a follow-up.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://cdn.tailwindcss.com https://unpkg.com https://ajax.googleapis.com https://www.gstatic.com https://cdnjs.cloudflare.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: blob: https:",
        "connect-src 'self' blob: https://unpkg.com https://ajax.googleapis.com https://www.gstatic.com",
        "worker-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        # SEC-4: modern equivalent of X-Frame-Options: DENY for all pages. The
        # embeddable viewer relaxes this per-response (see set_security_headers).
        "frame-ancestors 'none'",
    ]
)

# csrf, limiter and rate_limit_key are defined in extensions.py (imported above)
# so blueprints can attach rate limits without importing app.py.


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
            "canonical_url": canonical_url,
            "model_asset_token": model_asset_token,
            "license_plans": LICENSE_PLANS,
            "get_license_plan": get_license_plan,
            "model_resolver_url": model_resolver_url,
            "model_access_status": model_access_status,
            "format_model_dimensions_cm": format_model_dimensions_cm,
        }

    @app.after_request
    def set_security_headers(response):
        # Lightweight set of always-on hardening headers.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # SEC-4/AR-1: the public viewer in embed mode (?embed=1) is meant to be
        # framed by third-party sites, so it must NOT carry X-Frame-Options:DENY.
        # Instead we relax CSP frame-ancestors to a configurable allowlist for
        # those responses only; every other page stays frame-denied.
        embed_viewer = request.endpoint == "view_model" and request.args.get("embed")
        if not embed_viewer:
            response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if app.config.get("APP_ENV") in {"production", "prod", "pilot"}:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        if app.config.get("CSP_ENABLED", True):
            header_name = (
                "Content-Security-Policy-Report-Only"
                if app.config.get("CSP_REPORT_ONLY")
                else "Content-Security-Policy"
            )
            policy = CONTENT_SECURITY_POLICY
            if embed_viewer:
                ancestors = (app.config.get("EMBED_FRAME_ANCESTORS") or "*").strip()
                policy = policy.replace("frame-ancestors 'none'", f"frame-ancestors {ancestors}")
            response.headers.setdefault(header_name, policy)
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
        sync_configured_admins(app)
        ensure_sqlite_schema(app)
        stamp_alembic_version_if_needed(app)

    return app


SUPPORTED_MODEL_EXTENSIONS = {"stl", "glb", "obj", "fbx"}
COMPANION_FILE_EXTENSIONS = {".mtl", ".png", ".jpg", ".jpeg", ".webp"}
# Blog post inline images (admin upload). SVG is excluded on purpose (it can
# carry script). Raster formats only.
ALLOWED_BLOG_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_BLOG_IMAGE_BYTES = 10 * 1024 * 1024
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


def _model_glb_candidate_paths(model: Model3D) -> list[str]:
    paths: list[str] = []
    if model.glb_path:
        paths.append(model.glb_path)
        if not os.path.isabs(model.glb_path):
            paths.append(os.path.join(current_app.config["CONVERTED_FOLDER"], model.glb_path))
    if model.storage_key:
        paths.append(os.path.join(current_app.config["CONVERTED_FOLDER"], model.storage_key))
    return paths


def _glb_dimensions_from_accessors(glb_path: str) -> list[float] | None:
    """Read bounding-box extents (in GLB units) from POSITION accessor min/max,
    applying node world transforms. Works even on Draco-compressed geometry,
    since accessor min/max are preserved in the glTF JSON regardless of
    geometry compression. Returns [x, y, z] extents or None.
    """
    try:
        import numpy as np
        import pygltflib

        gltf = pygltflib.GLTF2().load(glb_path)
        if not gltf or not gltf.meshes:
            return None

        def node_local_matrix(node) -> "np.ndarray":
            if node.matrix:
                # glTF stores matrices column-major; reshape and transpose.
                return np.array(node.matrix, dtype=float).reshape(4, 4).T
            m = np.identity(4)
            if node.scale:
                m[0, 0], m[1, 1], m[2, 2] = node.scale
            t = np.identity(4)
            if node.translation:
                t[:3, 3] = node.translation
            r = np.identity(4)
            if node.rotation:
                x, y, z, w = node.rotation
                r[:3, :3] = [
                    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
                ]
            return t @ r @ m

        world_min = np.array([np.inf, np.inf, np.inf])
        world_max = np.array([-np.inf, -np.inf, -np.inf])

        scene = gltf.scenes[gltf.scene or 0] if gltf.scenes else None
        roots = scene.nodes if scene else list(range(len(gltf.nodes or [])))

        stack = [(idx, np.identity(4)) for idx in roots]
        while stack:
            idx, parent = stack.pop()
            node = gltf.nodes[idx]
            world = parent @ node_local_matrix(node)
            if node.mesh is not None:
                mesh = gltf.meshes[node.mesh]
                for prim in mesh.primitives:
                    pos = prim.attributes.POSITION
                    if pos is None:
                        continue
                    acc = gltf.accessors[pos]
                    if not acc.min or not acc.max:
                        continue
                    lo, hi = acc.min[:3], acc.max[:3]
                    corners = np.array([
                        [lo[0], lo[1], lo[2], 1], [hi[0], lo[1], lo[2], 1],
                        [lo[0], hi[1], lo[2], 1], [hi[0], hi[1], lo[2], 1],
                        [lo[0], lo[1], hi[2], 1], [hi[0], lo[1], hi[2], 1],
                        [lo[0], hi[1], hi[2], 1], [hi[0], hi[1], hi[2], 1],
                    ], dtype=float)
                    transformed = (world @ corners.T).T[:, :3]
                    world_min = np.minimum(world_min, transformed.min(axis=0))
                    world_max = np.maximum(world_max, transformed.max(axis=0))
            for child in (node.children or []):
                stack.append((child, world))

        if not np.all(np.isfinite(world_min)) or not np.all(np.isfinite(world_max)):
            return None
        return [float(axis) for axis in (world_max - world_min)]
    except Exception:
        logger.warning("Accessor-based dimension read failed for %s", glb_path, exc_info=True)
        return None


def compute_glb_dimensions_cm(glb_path: str | None) -> str | None:
    """Measure a GLB's bounding-box extents and return a formatted string like
    "12.3 x 4.5 x 6.7 cm", or None if it cannot be measured.

    Tries trimesh first (accurate for uncompressed GLBs), then falls back to
    reading POSITION accessor min/max — which survives Draco compression, so
    optimized GLBs and previously-stored models can still be measured.
    """
    if not glb_path or not os.path.exists(glb_path):
        return None

    extents = None
    try:
        import trimesh

        loaded = trimesh.load(glb_path, force="scene")
        bounds = getattr(loaded, "bounds", None)
        if bounds is not None:
            candidate = [float(axis) for axis in (bounds[1] - bounds[0])]
            if candidate and max(candidate) > 0:
                extents = candidate
    except Exception:
        logger.info("trimesh could not measure %s; trying accessor min/max", glb_path)

    if extents is None:
        extents = _glb_dimensions_from_accessors(glb_path)

    if not extents or max(extents) <= 0:
        return None
    extents_cm = [axis * 100 for axis in extents]
    return " x ".join(f"{axis:.1f}" for axis in extents_cm) + " cm"


def format_model_dimensions_cm(model: Model3D | None) -> str:
    if not model:
        return "Not measured"
    if getattr(model, "dimensions_cm", None):
        return model.dimensions_cm
    glb_path = next((path for path in _model_glb_candidate_paths(model) if path and os.path.exists(path)), None)
    measured = compute_glb_dimensions_cm(glb_path)
    if measured:
        try:
            model.dimensions_cm = measured
            db.session.commit()
        except Exception:
            db.session.rollback()
        return measured
    return "Not measured"


def human_scale_reference(dimensions_cm: str | None) -> str | None:
    """Return a real-world size reference like '~ credit card (8.5 cm)'."""
    if not dimensions_cm:
        return None
    try:
        parts = dimensions_cm.replace(" cm", "").split(" x ")
        longest = max(float(p.strip()) for p in parts)
    except (ValueError, TypeError):
        return None
    references = [
        (0.5, "grain of rice"),
        (1.5, "fingernail"),
        (3.0, "coin"),
        (5.0, "thumb"),
        (8.5, "credit card"),
        (15.0, "hand span"),
        (22.0, "A5 paper"),
        (30.0, "ruler"),
        (45.0, "laptop"),
        (60.0, "desk width"),
        (100.0, "arm span"),
        (180.0, "human height"),
    ]
    best = None
    best_dist = float("inf")
    for ref_cm, ref_name in references:
        dist = abs(longest - ref_cm)
        if dist < best_dist:
            best_dist = dist
            best = (ref_cm, ref_name)
    if best:
        return f"≈ {best[1]} ({best[0]} cm)"
    return None


def configured_admin_emails(app: Flask | None = None) -> set[str]:
    source = (app or current_app).config.get("ADMIN_EMAILS", [])
    if isinstance(source, str):
        source = source.split(",")
    return {str(email).strip().lower() for email in source if str(email).strip()}


def user_is_configured_admin(user: User | None, app: Flask | None = None) -> bool:
    if not user or not getattr(user, "email", None):
        return False
    return user.email.strip().lower() in configured_admin_emails(app)


def sync_configured_admins(app: Flask) -> None:
    emails = configured_admin_emails(app)
    if not emails:
        return
    try:
        users = User.query.filter(func.lower(User.email).in_(emails), User.is_admin.is_(False)).all()
        for user in users:
            user.is_admin = True
        if users:
            db.session.commit()
            logger.info("Promoted %s configured admin user(s).", len(users))
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not sync configured admin users")


def day_label(value: datetime) -> str:
    return value.strftime("%m-%d")


def month_label(value: datetime) -> str:
    return value.strftime("%Y-%m")


def scan_folder_size(path: str) -> tuple[int, int]:
    total_size = 0
    total_files = 0
    if not path or not os.path.exists(path):
        return total_size, total_files
    for root, _, files in os.walk(path):
        for filename in files:
            file_path = os.path.join(root, filename)
            try:
                total_size += os.path.getsize(file_path)
                total_files += 1
            except OSError:
                continue
    return total_size, total_files


def count_orphan_files(folder: str, expected_filenames: set[str]) -> int:
    if not folder or not os.path.exists(folder):
        return 0
    orphan_count = 0
    for root, _, files in os.walk(folder):
        for filename in files:
            file_path = os.path.abspath(os.path.join(root, filename))
            if file_path not in expected_filenames:
                orphan_count += 1
    return orphan_count


def backup_folder(app: Flask) -> str:
    path = os.path.join(app.config["STORAGE_ROOT"], "admin_backups")
    os.makedirs(path, exist_ok=True)
    return path


def list_backup_archives(app: Flask) -> list[dict]:
    folder = backup_folder(app)
    backups = []
    for filename in os.listdir(folder):
        if not filename.endswith(".zip"):
            continue
        file_path = os.path.join(folder, filename)
        try:
            stat = os.stat(file_path)
        except OSError:
            continue
        backups.append(
            {
                "filename": filename,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, UTC),
            }
        )
    return sorted(backups, key=lambda item: item["created_at"], reverse=True)


def add_folder_to_zip(zip_file: zipfile.ZipFile, folder: str, archive_prefix: str) -> int:
    added = 0
    if not folder or not os.path.exists(folder):
        return added
    for root, _, files in os.walk(folder):
        for filename in files:
            file_path = os.path.join(root, filename)
            arcname = os.path.join(archive_prefix, os.path.relpath(file_path, folder))
            zip_file.write(file_path, arcname)
            added += 1
    return added


def _dump_postgres_into_zip(zip_file: zipfile.ZipFile, db_uri: str) -> bool:
    """Best-effort ``pg_dump`` of a PostgreSQL database into the backup archive.

    Returns True only when a SQL dump was actually written. Never raises: a
    non-PostgreSQL URI, a missing ``pg_dump`` binary, or a failed dump just logs
    and returns False so the rest of the backup (files) still completes. The
    dump is a plain-text ``pg_restore``/``psql``-loadable SQL script.
    """
    if "postgres" not in db_uri:
        return False
    # SQLAlchemy uses a driver-qualified scheme (postgresql+psycopg://); pg_dump
    # needs a plain libpq URL.
    libpq_url = re.sub(r"^postgres(?:ql)?\+\w+://", "postgresql://", db_uri)
    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        logger.warning("pg_dump not found on PATH; database NOT included in backup")
        return False
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
            tmp_path = tmp.name
        result = subprocess.run(
            [pg_dump, "--no-owner", "--no-privileges", "--file", tmp_path, libpq_url],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.warning("pg_dump failed (rc=%s): %s", result.returncode, result.stderr.strip())
            return False
        zip_file.write(tmp_path, "database/postgres_dump.sql")
        logger.info("pg_dump included in backup archive")
        return True
    except Exception:
        logger.warning("pg_dump errored; database NOT included in backup", exc_info=True)
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def create_backup_archive(app: Flask, created_by_user_id: int | None = None, reason: str = "manual") -> str:
    folder = backup_folder(app)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"academic_ar_backup_{timestamp}.zip"
    archive_path = os.path.join(folder, filename)
    manifest_lines = [
        "format_version=1",
        f"created_at={datetime.now(UTC).isoformat()}",
        f"created_by_user_id={created_by_user_id or ''}",
        f"reason={reason}",
        f"site_url={app.config.get('SITE_URL', '')}",
    ]
    qr_manifest = []
    for qr in QRLink.query.order_by(QRLink.created_at.asc()).all():
        qr_manifest.append(
            f"{qr.public_id}\t{qr.status}\t{model_resolver_url(qr.model)}\t{qr.model_id}\t{qr.target_type}"
        )
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if db_uri.startswith("sqlite:///"):
            db_path = db_uri.replace("sqlite:///", "", 1)
            if os.path.exists(db_path):
                zip_file.write(db_path, "database/academic_ar.db")
                manifest_lines.append("database=sqlite")
        elif _dump_postgres_into_zip(zip_file, db_uri):
            manifest_lines.append("database=postgres_dump")
        else:
            manifest_lines.append("database=unavailable")
        manifest_lines.append(f"uploads_files={add_folder_to_zip(zip_file, app.config['UPLOAD_FOLDER'], 'uploads')}")
        manifest_lines.append(f"converted_files={add_folder_to_zip(zip_file, app.config['CONVERTED_FOLDER'], 'converted')}")
        manifest_lines.append(f"qr_files={add_folder_to_zip(zip_file, app.config['QR_FOLDER'], 'qr_codes')}")
        manifest_lines.append(f"pdf_files={add_folder_to_zip(zip_file, app.config['PDF_FOLDER'], 'pdfs')}")
        zip_file.writestr("manifest.txt", "\n".join(manifest_lines) + "\n")
        zip_file.writestr("qr_links.tsv", "public_id\tstatus\tresolver_url\tmodel_id\ttarget_type\n" + "\n".join(qr_manifest) + "\n")
    log_audit(
        "admin_backup_created",
        user_id=created_by_user_id,
        resource_id=filename,
        details={"reason": reason, "filename": filename},
    )
    mirror_file(archive_path, f"admin_backups/{filename}")
    return filename


def ensure_daily_backup(app: Flask, created_by_user_id: int | None = None) -> str | None:
    today_prefix = f"academic_ar_backup_{datetime.now(UTC).strftime('%Y%m%d')}"
    if any(item["filename"].startswith(today_prefix) for item in list_backup_archives(app)):
        return None
    return create_backup_archive(app, created_by_user_id=created_by_user_id, reason="daily")


def validate_secret_key(app: Flask) -> None:
    app_env = str(app.config.get("APP_ENV", "development")).lower()
    secret_key = app.config.get("SECRET_KEY")
    if app_env in {"production", "prod", "pilot"} and (
        not secret_key or secret_key == "dev-secret-change-in-production"
    ):
        raise RuntimeError("SECRET_KEY must be set for pilot/production environments.")


def ensure_sqlite_schema(app: Flask) -> None:
    """Add newer columns to legacy SQLite DBs that predate them (papers
    soft-delete columns, models.dimensions_cm).

    Idempotent: each ALTER is guarded by a PRAGMA column check, so running this
    on an already-migrated (or freshly create_all'd) database is a no-op. Only
    needed for SQLite because the corresponding Alembic migration skips
    add_column on SQLite.
    """
    if not str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).startswith("sqlite"):
        return
    with db.engine.begin() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(papers)")).fetchall()}
        if "deleted_at" not in columns:
            connection.execute(text("ALTER TABLE papers ADD COLUMN deleted_at DATETIME"))
        if "deleted_by_user_id" not in columns:
            connection.execute(text("ALTER TABLE papers ADD COLUMN deleted_by_user_id INTEGER"))
        model_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(models)")).fetchall()}
        if model_columns and "dimensions_cm" not in model_columns:
            connection.execute(text("ALTER TABLE models ADD COLUMN dimensions_cm VARCHAR(50)"))
        payment_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(payments)")).fetchall()}
        if payment_columns and "model_id" not in payment_columns:
            connection.execute(text("ALTER TABLE payments ADD COLUMN model_id VARCHAR(36)"))
        if payment_columns and "plan_key" not in payment_columns:
            connection.execute(text("ALTER TABLE payments ADD COLUMN plan_key VARCHAR(30)"))


def _alembic_head_revision(app: Flask) -> str | None:
    """Resolve the current Alembic head revision from the migrations/ folder.

    Returns None if it cannot be determined (e.g. migrations dir missing).
    """
    try:
        from alembic.script import ScriptDirectory

        migrations_dir = os.path.join(app.root_path, "migrations")
        if not os.path.isdir(migrations_dir):
            return None
        script = ScriptDirectory(migrations_dir)
        return script.get_current_head()
    except Exception:
        logger.exception("Could not determine Alembic head revision")
        return None


def stamp_alembic_version_if_needed(app: Flask) -> None:
    """
    Stamp a non-SQLite database (e.g. PostgreSQL on Railway) with the CURRENT
    Alembic head revision when tables already exist but ``alembic_version`` is
    missing.

    The schema is bootstrapped by ``db.create_all()`` (the project's de-facto
    baseline), so it already matches the latest models. Stamping to the head
    revision — resolved dynamically instead of a hardcoded id — tells Alembic
    the DB is fully up to date and prevents ``flask db upgrade`` from trying to
    re-apply migrations whose columns/indexes already exist.
    """
    import sqlalchemy as sa
    from sqlalchemy import text
    try:
        with app.app_context():
            # Only relevant for non-SQLite databases (PostgreSQL on Railway).
            if db.engine.dialect.name == "sqlite":
                return

            inspector = sa.inspect(db.engine)
            tables = inspector.get_table_names()

            # Schema is populated but alembic_version has not been created yet.
            if "models" in tables and "alembic_version" not in tables:
                head = _alembic_head_revision(app)
                if not head:
                    logger.warning(
                        "Tables exist without alembic_version, but the Alembic head "
                        "revision could not be resolved; skipping stamp."
                    )
                    return
                logger.info(
                    "Tables found but no alembic_version table. Stamping to head '%s'...",
                    head,
                )
                with db.engine.begin() as connection:
                    connection.execute(
                        text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
                    )
                    connection.execute(
                        text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
                        {"rev": head},
                    )
                logger.info("Successfully stamped alembic_version to '%s'.", head)
    except Exception as e:
        logger.error(f"Error checking or stamping alembic version: {e}")


# rate_limit_key() is imported from extensions.py.


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
        if counter > 10000:
            slug = f"{base}-{uuid.uuid4().hex[:8]}"
            break
        slug = f"{base}-{counter}"
    return slug


def make_blog_slug(title: str, exclude_id: int | None = None) -> str:
    """Unique blog slug from a title, avoiding both DB and built-in code slugs."""
    reserved = code_post_slugs()
    base = slugify(title)[:200] or "post"
    slug = base
    counter = 1
    while True:
        existing = BlogPost.query.filter_by(slug=slug).first()
        clashes = (existing is not None and existing.id != exclude_id) or slug in reserved
        if not clashes:
            return slug
        counter += 1
        if counter > 10000:
            return f"{base}-{uuid.uuid4().hex[:8]}"
        slug = f"{base}-{counter}"


def _db_blogpost_to_view(bp: BlogPost) -> dict:
    """Normalize a DB BlogPost to the dict shape the blog templates expect."""
    created = bp.created_at or datetime.now(UTC)
    return {
        "slug": bp.slug,
        "title": bp.title,
        "description": bp.description or "",
        "date": created.strftime("%Y-%m-%d"),
        "author": bp.author or "AcademicAR Team",
        "tags": [t.strip() for t in (bp.tags or "").split(",") if t.strip()],
        "persona": bp.persona or "",
        "read_minutes": bp.read_minutes or 4,
        "body": bp.body,
        "source": "db",
        "id": bp.id,
        "updated_at": bp.updated_at,
    }


def _code_blogpost_to_view(p: dict) -> dict:
    return {**p, "tags": list(p.get("tags") or []), "source": "code"}


def merged_blog_posts() -> list[dict]:
    """Published DB posts + built-in code posts, newest first (DB wins on slug)."""
    db_views = [_db_blogpost_to_view(b) for b in BlogPost.query.filter_by(is_published=True).all()]
    db_slugs = {v["slug"] for v in db_views}
    code_views = [_code_blogpost_to_view(p) for p in get_all_posts() if p["slug"] not in db_slugs]
    return sorted(db_views + code_views, key=lambda v: v["date"], reverse=True)


def find_blog_post(slug: str) -> dict | None:
    bp = BlogPost.query.filter_by(slug=slug, is_published=True).first()
    if bp is not None:
        return _db_blogpost_to_view(bp)
    code_post = get_post(slug)
    return _code_blogpost_to_view(code_post) if code_post else None


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
        "Abstract": (abstract, 5000),
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


def paper_is_expired(paper: Paper) -> bool:
    return licensing_paper_is_expired(paper)


def paper_is_deleted(paper: Paper | None) -> bool:
    return not paper or (paper.status or "active").lower() == "deleted"


def active_paper_query():
    return Paper.query.filter(or_(Paper.status.is_(None), Paper.status != "deleted"))


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
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            qr_link = QRLink.query.filter_by(model_id=model.id, target_type="model_viewer").first()
            if qr_link is None:
                raise
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
    qr_path = os.path.join(qr_folder, filename)
    img.save(qr_path)
    mirror_file(qr_path, f"qr_codes/{filename}")
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
    mirror_directory(str(archive_root), f"uploads/{model.id}/v{version}")
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


def model_asset_token(model) -> str:
    """Short cache-busting token for a model's served GLB/USDZ.

    <model-viewer> caches the model by its src URL, so recoloring or rescaling a
    model (which rewrites the same /files/<id>/model.glb path) would otherwise
    keep showing the stale version. Appending ?v=<token> makes the URL change
    whenever the asset changes, forcing a re-fetch.
    """
    raw = "|".join(
        str(getattr(model, attr, "") or "")
        for attr in (
            "appearance_color",
            "appearance_roughness",
            "appearance_metallic",
            "dimensions_cm",
            "version",
            "replaced_at",
            "file_size",
        )
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def canonical_url() -> str:
    """Absolute canonical URL for the current request.

    Built from SITE_URL (never the raw Host header) and the request path with
    the query string dropped, so search engines see one canonical URL per page.
    """
    site_url = (current_app.config.get("SITE_URL") or "").strip().rstrip("/")
    path = request.path or "/"
    if site_url:
        return f"{site_url}{path}"
    return request.base_url


# Single source of truth for the public FAQ: rendered as visible Q&A AND as
# FAQPage JSON-LD (rich result) on /faq, so the two never drift apart.
FAQ_ITEMS = (
    {
        "q": "What is AcademicAR?",
        "a": "AcademicAR turns the 3D models behind your research into an "
             "interactive web viewer with augmented reality and a permanent QR "
             "code, so readers can explore your specimen, molecule, or artifact "
             "from a paper, thesis, poster, or slide.",
    },
    {
        "q": "Which 3D file formats can I upload?",
        "a": "You can upload GLB, STL, OBJ, and FBX files. We automatically "
             "convert and optimize them to web-friendly GLB (Draco-compressed "
             "geometry and compressed textures) and generate an iOS-ready USDZ "
             "for AR.",
    },
    {
        "q": "Do my readers need to install an app to view the model?",
        "a": "No. The viewer runs in any modern browser. On most phones and "
             "tablets, readers can also tap to place the model in augmented "
             "reality without installing anything.",
    },
    {
        "q": "What is the QR code for, and does it keep working?",
        "a": "Every model gets a stable QR code and short link. The QR resolves "
             "to the same model even after you replace the file, change its "
             "color, or upgrade its license, so printed posters and PDFs never "
             "break.",
    },
    {
        "q": "How long does the model stay online?",
        "a": "Access depends on the model's plan: Free access lasts 3 days, "
             "Academic gives 3 years, and Extended Archive keeps it online for "
             "10 years. You can upgrade a model at any time without changing its "
             "link or QR code.",
    },
    {
        "q": "Can I embed the viewer in my website or institutional repository?",
        "a": "Yes. Public model viewers can be embedded as a lightweight iframe "
             "widget, so you can place the interactive 3D model on a lab page, "
             "blog, or repository alongside your publication.",
    },
    {
        "q": "Who owns the uploaded models and data?",
        "a": "You retain ownership of everything you upload. Uploading requires "
             "confirming that the content is anonymized where needed and that "
             "you hold the rights to share it.",
    },
    {
        "q": "Is AcademicAR suitable for anatomy, biology, chemistry, and archaeology?",
        "a": "Yes. AcademicAR works across disciplines — anatomical and medical "
             "models, molecular structures, biological specimens, archaeological "
             "and paleontological scans, geological samples, and engineering "
             "parts all render in the same interactive viewer.",
    },
)


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
    mirror_file(full_path, f"qr_codes/{filename}")
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
        paths.append(("file", os.path.join(app.config["PDF_FOLDER"], os.path.basename(paper.pdf_path))))
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


def finalize_converted_glb(glb_path: str, *, source_dir: str) -> None:
    """Pack texture references, ensure baseline PBR materials, optimize, and validate GLB output."""
    embed_external_textures(glb_path, search_dirs=[source_dir, os.path.dirname(glb_path)])
    # Normalize any KHR_materials_pbrSpecularGlossiness materials (FBX-derived or
    # legacy GLB uploads) to core metallic-roughness so model-viewer renders
    # their textures. Best-effort; runs before optimize so webp compression and
    # prune operate on the final material set.
    normalize_specular_glossiness(glb_path)
    ensure_pbr_materials(glb_path)
    optimize_glb(glb_path)
    validate_glb_quality(glb_path)


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

            # Measure bounding-box dimensions from the *uncompressed* GLB before
            # finalize_converted_glb() applies Draco compression. trimesh cannot
            # read Draco-compressed geometry, so measuring afterwards yields
            # "Not measured". Captured here and cached on the model row below.
            measured_dimensions_cm = compute_glb_dimensions_cm(target_glb)

            try:
                finalize_converted_glb(target_glb, source_dir=os.path.dirname(source_path))
            except GLBQualityError as e:
                cleanup_file(target_glb)
                mark_model_failed(
                    model_id,
                    "Conversion quality check failed: " + str(e),
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
            # Dimensions were measured above from the uncompressed GLB (trimesh
            # cannot read the Draco-compressed output). Fall back to a post-
            # finalize measurement only if the pre-compression read failed.
            model.dimensions_cm = measured_dimensions_cm or compute_glb_dimensions_cm(glb_path)
            poster_png = os.path.join(os.path.dirname(glb_path), "poster.png")
            if generate_poster(glb_path, poster_png):
                model.poster_path = poster_png
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
            mirror_directory(os.path.join(app.config["CONVERTED_FOLDER"], model_id), f"converted/{model_id}")
        except Exception:
            db.session.rollback()
            logger.exception("Background model processing failed")
            cleanup_file(target_glb)
            mark_model_failed(
                model_id,
                "Unexpected conversion error. Please check the file and try again.",
                is_replacement=is_replacement,
                job=job,
                version=version,
            )
            cleanup_dir(upload_dir)
            if not is_replacement:
                cleanup_dir(converted_dir)


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
        # Tests and explicit local dev (DEV_INLINE_JOBS=1) run conversion inline
        # so uploads appear immediately without a separate worker process.
        process_model_upload_job(app, **job_kwargs)
    # Otherwise the ConversionJob row stays "pending" for the isolated worker
    # (worker.py / run_next_conversion_job) to claim. Production web processes
    # MUST NOT run CPU/RAM-heavy 3D conversions inline; doing so here would also
    # race the worker for the same job.
    return job


def run_next_conversion_job(app: Flask) -> bool:
    """Pick up the oldest pending ConversionJob and run it.

    Used by ``worker.py`` for the Railway worker service. Returns True when a
    job was processed (or attempted), so the caller can immediately poll for
    the next job rather than sleeping.
    """
    with app.app_context():
        # Claim the oldest pending job atomically. On PostgreSQL/MySQL we take a
        # row lock with SKIP LOCKED so multiple concurrent workers never grab the
        # same job. SQLite ignores FOR UPDATE (single-writer), which is fine for
        # local/dev single-worker setups.
        query = (
            ConversionJob.query
            .filter(ConversionJob.status == "pending")
            .order_by(ConversionJob.created_at.asc())
        )
        if db.engine.dialect.name in {"postgresql", "mysql"}:
            query = query.with_for_update(skip_locked=True)
        job = query.first()
        if job is None:
            return False
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
    display_name = (display_name or "").strip()[:255] or None
    description = (description or "").strip()[:5000] or None
    color = (color or "").strip() or None
    if color and HEX_COLOR_PATTERN.fullmatch(color) is None:
        color = None
    # Source-unit controls are intentionally disabled in the UI. STL files are
    # unitless, so we auto-detect the scale from the raw mesh extents (µm/mm/cm/m
    # bands + safety clamp in STLConverter) instead of assuming meters — that
    # assumption made cm-authored models render ~100x too large.
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
        flash("Too many attempts in a short time. Please wait a few minutes and try again.", "warning")
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
    def require_admin() -> None:
        if not current_user.is_authenticated:
            abort(403)
        if user_is_configured_admin(current_user, app) and not current_user.is_admin:
            current_user.is_admin = True
            db.session.commit()
        if not current_user.is_admin:
            abort(403)

    @app.route("/health")
    def health():
        try:
            db.session.execute(db.text("SELECT 1"))
            return jsonify({"status": "ok"}), 200
        except Exception:
            return jsonify({"status": "error"}), 500

    @app.route("/")
    def landing():
        return render_template("landing.html")

    @app.route("/pricing")
    def pricing():
        return render_template("pricing.html", license_plans=LICENSE_PLANS)

    @app.route("/demo/mitochondria/ar")
    def demo_mitochondria_ar():
        log_audit("demo_mitochondria_ar_opened", details={"source": request.args.get("source") or "direct"})
        demo_model = types.SimpleNamespace(
            id="demo-mitochondria",
            display_name="Mitochondria",
            original_filename="mitochondria.glb",
            file_size=2_400_000,
            ar_placement="floor",
            poster_path=None,
            description="Cross-section of a mitochondrion showing the inner membrane cristae, outer membrane, and matrix. Prepared for interactive 3D visualization and augmented reality placement in academic publications.",
            appearance_color=None,
        )
        demo_paper = types.SimpleNamespace(
            title="Cellular Organelle Morphology in Human Hepatocytes",
            authors="Dr. Sarah Chen, Prof. James Miller, Dr. Ayşe Yılmaz",
            year=2025,
            field="Cell Biology",
            institution="ETH Zurich — Department of Biology",
            doi="10.1234/cellbio.2025.mito",
            pmid="39281047",
            abstract="This study presents high-resolution 3D reconstructions of mitochondrial ultrastructure in human hepatocytes using serial block-face scanning electron microscopy. Interactive models enable spatial exploration of cristae morphology and membrane topology, bridging the gap between 2D micrographs and volumetric understanding.",
            slug="demo-mitochondria",
            is_public=True,
        )
        return render_template(
            "viewer.html",
            model=demo_model,
            paper=demo_paper,
            has_usdz=False,
            annotations=[],
            scale_reference="About the size of a tennis ball (scaled 50,000×)",
            dimensions_cm="5.2 × 3.1 × 2.8 cm",
            is_owner=False,
            demo_mode=True,
        )

    @app.route("/demo/mitochondria/qr.png")
    def demo_mitochondria_qr():
        import io
        import qrcode
        from qrcode.image.styledpil import StyledPilImage
        from qrcode.image.styles.moduledrawers import RoundedModuleDrawer
        from qrcode.image.styles.colormasks import SolidFillColorMask

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(public_url("demo_mitochondria_ar", source="hero_qr"))
        qr.make(fit=True)
        img = qr.make_image(
            image_factory=StyledPilImage,
            module_drawer=RoundedModuleDrawer(),
            color_mask=SolidFillColorMask(back_color=(255, 255, 255), front_color=(0, 0, 0))
        )
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        response = current_app.response_class(buffer.getvalue(), mimetype="image/png")
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    @app.route("/terms")
    def terms():
        return render_template("legal/terms.html")

    @app.route("/privacy")
    def privacy():
        return render_template("legal/privacy.html")

    @app.route("/data-protection")
    def data_protection():
        return render_template("legal/data_protection.html")

    @app.route("/faq")
    def faq():
        return render_template("faq.html", faqs=FAQ_ITEMS)

    @app.route("/about")
    def about():
        return render_template("about.html")

    @app.route("/ar-for")
    def ar_for_index():
        """Hub page linking every discipline landing page (topic-cluster center)."""
        return render_template("disciplines_index.html", disciplines=all_disciplines())

    @app.route("/ar-for-<discipline>")
    def ar_for(discipline):
        """Programmatic SEO landing page for one field, e.g. /ar-for-anatomy."""
        data = get_discipline(discipline)
        if not data:
            abort(404)
        return render_template(
            "discipline.html",
            discipline=data,
            related=related_disciplines(data["slug"]),
        )

    @app.route("/contact", methods=["GET", "POST"])
    @limiter.limit("5 per hour", methods=["POST"])
    def contact():
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip()
            message = (request.form.get("message") or "").strip()
            if not name or not email or not message:
                flash("Please fill in your name, email, and message.", "danger")
                return redirect(url_for("contact"))
            from utils.email import send_email

            recipient = current_app.config.get("CONTACT_EMAIL") or "info@academicar.com"
            send_email(
                recipient,
                "AcademicAR contact form",
                f"From: {name} <{email}>\n\n{message}",
            )
            log_audit("contact_message_submitted", details={"from_email": email})
            flash("Thanks — your message has been sent. We'll get back to you soon.", "success")
            return redirect(url_for("contact"))
        return render_template("contact.html")

    @app.route("/blog")
    def blog_index():
        return render_template("blog_list.html", posts=merged_blog_posts())

    @app.route("/blog/<slug>")
    def blog_post(slug):
        post = find_blog_post(slug)
        if not post:
            abort(404)
        cache_key = (
            f"db:{post['id']}:{post['updated_at']}" if post.get("source") == "db" else post["slug"]
        )
        return render_template(
            "blog_post.html",
            post=post,
            body_html=render_body(post["body"], cache_key=cache_key),
        )

    @app.route("/blog-images/<path:filename>")
    def serve_blog_image(filename):
        safe = os.path.basename(filename)
        local = os.path.join(app.config["BLOG_IMAGE_FOLDER"], safe)
        if not os.path.exists(local):
            ensure_local(local, f"blog_images/{safe}")
        if not os.path.exists(local):
            abort(404)
        response = send_from_directory(app.config["BLOG_IMAGE_FOLDER"], safe, conditional=True)
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    @app.route("/robots.txt")
    def robots_txt():
        body = "\n".join(
            [
                "User-agent: *",
                "Allow: /",
                "Disallow: /admin",
                "Disallow: /dashboard",
                "Disallow: /profile",
                "Disallow: /account",
                "Disallow: /auth",
                "Disallow: /papers",
                "Disallow: /models",
                "Disallow: /qr-print",
                "Disallow: /pdfs",
                "Disallow: /payment",
                f"Sitemap: {public_url('sitemap_xml')}",
                "",
            ]
        )
        return current_app.response_class(body, mimetype="text/plain")

    @app.route("/sitemap.xml")
    def sitemap_xml():
        from xml.sax.saxutils import escape

        urls: list[str] = []
        for endpoint in (
            "landing",
            "pricing",
            "faq",
            "about",
            "ar_for_index",
            "contact",
            "blog_index",
            "terms",
            "privacy",
            "data_protection",
            "demo_mitochondria_ar",
        ):
            try:
                urls.append(public_url(endpoint))
            except Exception:
                continue
        for slug in discipline_slugs():
            try:
                urls.append(public_url("ar_for", discipline=slug))
            except Exception:
                continue
        for post in merged_blog_posts():
            try:
                urls.append(public_url("blog_post", slug=post["slug"]))
            except Exception:
                continue
        # Public, non-deleted publication pages are indexable SEO assets.
        public_papers = (
            Paper.query.filter(Paper.is_public.is_(True))
            .filter(db.or_(Paper.status.is_(None), Paper.status != "deleted"))
            .order_by(Paper.created_at.desc())
            .limit(5000)
            .all()
        )
        for paper in public_papers:
            try:
                urls.append(public_url("paper_public", slug=paper.slug))
            except Exception:
                continue
        items = "".join(f"<url><loc>{escape(u)}</loc></url>" for u in urls)
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{items}</urlset>"
        )
        return current_app.response_class(xml, mimetype="application/xml")

    @app.route("/models/<model_id>/upgrade/<plan>", methods=["POST"])
    @login_required
    @require_model_ownership
    @limiter.limit("10 per hour", methods=["POST"])
    def upgrade_model_license(model_id, plan):
        """Start a paid upgrade of a single model to a longer access window.

        Provider-agnostic: a pending Payment is created, then the configured
        provider returns either an internal success URL (dev provider settles
        instantly) or an external hosted-checkout URL. The license is granted in
        :func:`apply_successful_payment` — here for the dev provider, or later
        from the webhook for a real Merchant of Record.
        """
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        plan_key = (plan or "").strip().lower()
        if plan_key not in PAID_PLAN_KEYS:
            flash("Choose a valid paid plan to upgrade this model.", "danger")
            return redirect(request.referrer or url_for("dashboard"))

        provider = get_payment_provider()
        payment = Payment(
            user_id=current_user.id,
            paper_id=model.paper_id,
            model_id=model.id,
            plan_key=plan_key,
            amount_kurus=plan_amount_minor_units(plan_key),
            currency=current_app.config.get("PAYMENT_CURRENCY", "USD"),
            provider=provider.name,
            provider_reference=f"{provider.name}-{uuid.uuid4().hex[:12]}",
            status="pending",
        )
        db.session.add(payment)
        try:
            db.session.flush()
            payment.invoice_number = build_invoice_number(payment.id)
            success_url = public_url("view_model", model_id=model.id)
            checkout_url = provider.create_checkout(
                payment=payment,
                model=model,
                plan_key=plan_key,
                user=current_user,
                success_url=success_url,
                cancel_url=success_url,
            )
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not start the upgrade. Please try again.", "danger")
            return redirect(request.referrer or url_for("dashboard"))

        if not checkout_url:
            flash(
                "Online payment isn't configured yet. Please contact support to "
                "upgrade this model.",
                "warning",
            )
            return redirect(url_for("view_model", model_id=model.id))

        log_audit(
            "model_upgrade_initiated",
            user_id=current_user.id,
            resource_id=model.id,
            details={"plan": plan_key, "provider": provider.name, "payment_id": payment.id},
        )
        if payment.status == "paid":
            flash("Upgrade complete — this model's access window has been extended.", "success")
        return redirect(checkout_url)

    @app.route("/payment/webhook/<provider_name>", methods=["POST"])
    @csrf.exempt
    def payment_webhook(provider_name):
        """Receive a provider's 'order paid' webhook and grant the license.

        Idempotent: a duplicate event for an already-paid order is a no-op. The
        plan is restricted to user-buyable paid plans so a forged webhook can
        never grant the institutional tier.
        """
        provider = get_payment_provider()
        if provider.name != (provider_name or "").strip().lower():
            abort(404)
        if not provider.verify_webhook(request):
            abort(400)

        # Some gateways (PayTR) require a literal "OK" acknowledgement; others
        # accept JSON. Build the right ack for whichever resolved.
        def ack(**payload):
            if getattr(provider, "webhook_ack", None) is not None:
                return current_app.response_class(provider.webhook_ack, mimetype="text/plain")
            return jsonify({"ok": True, **payload}), 200

        event = provider.parse_event(request) or {}
        if (event.get("status") or "").lower() != "paid":
            return ack(ignored=True)

        provider_reference = event.get("provider_reference")
        plan_key = (event.get("plan_key") or "").strip().lower()
        effective_plan = plan_key if plan_key in PAID_PLAN_KEYS else None
        model_id = event.get("model_id")

        payment = None
        payment_id = event.get("payment_id")
        if payment_id is not None:
            try:
                payment = db.session.get(Payment, int(payment_id))
            except (TypeError, ValueError):
                payment = None
        if payment is None and provider_reference:
            payment = Payment.query.filter_by(provider_reference=provider_reference).first()
        if payment is not None and payment.status == "paid":
            return ack(duplicate=True)

        model = db.session.get(Model3D, model_id) if model_id else (payment.model if payment else None)

        if payment is None:
            payment = Payment(
                user_id=(model.user_id if model else None),
                paper_id=(model.paper_id if model else None),
                model_id=(model.id if model else None),
                plan_key=effective_plan,
                amount_kurus=plan_amount_minor_units(effective_plan) if effective_plan else 0,
                currency=current_app.config.get("PAYMENT_CURRENCY", "USD"),
                provider=provider.name,
                provider_reference=provider_reference,
                status="pending",
            )
            db.session.add(payment)
            db.session.flush()
            if not payment.invoice_number:
                payment.invoice_number = build_invoice_number(payment.id)
        elif provider_reference and not payment.provider_reference:
            payment.provider_reference = provider_reference

        # Recover the plan from the stored Payment when the callback omits it
        # (PayTR's notification carries no custom data).
        if effective_plan is None and payment is not None and (payment.plan_key or "") in PAID_PLAN_KEYS:
            effective_plan = payment.plan_key

        apply_successful_payment(payment, model, effective_plan)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify({"ok": False}), 500

        log_audit(
            "payment_completed",
            user_id=(model.user_id if model else None),
            resource_id=(model.id if model else None),
            details={
                "provider": provider.name,
                "plan": effective_plan,
                "payment_id": payment.id,
                "provider_reference": provider_reference,
            },
        )
        return ack()

    @app.route("/view/<model_id>")
    def view_model(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        if not _paper_visible_to_request(model.paper):
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
        log_audit(
            "public_model_viewed",
            user_id=current_user.id if current_user.is_authenticated else None,
            resource_id=model.id,
            details={"paper_id": model.paper_id, "public_id": model.public_id},
        )
        annotations = ModelAnnotation.query.filter_by(model_id=model.id).order_by(ModelAnnotation.order_index).all()
        scale_ref = human_scale_reference(format_model_dimensions_cm(model))
        is_owner = current_user.is_authenticated and current_user.id == model.user_id
        return render_template(
            "viewer.html", model=model, paper=model.paper, has_usdz=has_usdz,
            annotations=annotations, scale_reference=scale_ref,
            dimensions_cm=format_model_dimensions_cm(model),
            is_owner=is_owner,
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
            log_audit(
                "qr_resolved",
                resource_id=qr_link.public_id,
                details={"model_id": model.id, "target_type": qr_link.target_type},
            )
        if not _paper_visible_to_request(model.paper):
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
        return redirect(url_for("view_model", model_id=model.id))

    @app.route("/files/<unique_id>/<path:filename>")
    def serve_glb(unique_id, filename):
        if not is_uuid(unique_id) or filename not in {"model.glb", "model.usdz"}:
            abort(404)
        model = db.session.get(Model3D, unique_id)
        if not model:
            abort(404)
        if not _paper_visible_to_request(model.paper) or not model_is_accessible(model):
            abort(404)
        directory = os.path.join(app.config["CONVERTED_FOLDER"], unique_id)
        target = os.path.join(directory, filename)
        if not os.path.exists(target):
            # Local volume is ephemeral on Railway; restore from the R2 mirror.
            ensure_local(target, f"converted/{unique_id}/{filename}")
        if not os.path.exists(target):
            abort(404)
        mimetype = (
            "model/vnd.usdz+zip" if filename == "model.usdz" else "model/gltf-binary"
        )
        # send_from_directory adds ETag + Last-Modified and honours
        # If-None-Match / If-Modified-Since (conditional=True by default), so a
        # repeat view gets a cheap 304 instead of re-downloading the whole GLB.
        response = send_from_directory(directory, filename, mimetype=mimetype, conditional=True)
        response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
        # "private, no-cache" = never cached by shared/CDN caches and the browser
        # must revalidate every time (so our access checks above always run), but
        # it MAY keep the bytes and reuse them on a 304. A replace/appearance
        # update rewrites the file, changing the ETag, which invalidates the copy.
        response.headers["Cache-Control"] = "private, no-cache"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response

    @app.route("/files/<unique_id>/poster.png")
    def serve_poster(unique_id):
        if not is_uuid(unique_id):
            abort(404)
        model = db.session.get(Model3D, unique_id)
        if not model or not model.poster_path:
            abort(404)
        if not _paper_visible_to_request(model.paper) or not model_is_accessible(model):
            abort(404)
        directory = os.path.join(app.config["CONVERTED_FOLDER"], unique_id)
        poster = os.path.join(directory, "poster.png")
        if not os.path.exists(poster):
            ensure_local(poster, f"converted/{unique_id}/poster.png")
        if not os.path.exists(poster):
            abort(404)
        response = send_from_directory(directory, "poster.png", mimetype="image/png", conditional=True)
        response.headers["Cache-Control"] = "private, no-cache"
        return response

    @app.route("/qr-image/<model_id>")
    def qr_image(model_id):
        model = db.session.get(Model3D, model_id)
        if not model or not model.qr_code_path:
            abort(404)
        if not model.paper:
            abort(404)
        if paper_is_expired(model.paper):
            abort(404)
        if not _paper_visible_to_request(model.paper):
            abort(404)
        qr_name = os.path.basename(model.qr_code_path)
        ensure_local(os.path.join(app.config["QR_FOLDER"], qr_name), f"qr_codes/{qr_name}")
        return send_from_directory(app.config["QR_FOLDER"], qr_name)

    @app.route("/qr-print/<model_id>")
    @login_required
    def qr_print(model_id):
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        if not model.paper:
            abort(404)
        if paper_is_expired(model.paper):
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
        pdf_name = os.path.basename(paper.pdf_path)
        ensure_local(os.path.join(app.config["PDF_FOLDER"], pdf_name), f"pdfs/{pdf_name}")
        return send_from_directory(app.config["PDF_FOLDER"], pdf_name)

    def _paper_visible_to_request(paper: Paper) -> bool:
        """A paper is visible if it is public, or if the current user is the owner."""
        if paper_is_deleted(paper):
            return False
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
        if not paper or paper_is_deleted(paper):
            abort(404)
        if paper.user_id != current_user.id:
            abort(403)
        ensure_paper_qr(paper)
        return render_template("qr_page_paper.html", paper=paper)

    @app.route("/p/<slug>")
    def paper_public(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()
        if paper_is_expired(paper):
            abort(404)
        if not _paper_visible_to_request(paper):
            abort(404)
        return render_template("paper_public.html", paper=paper)

    @app.route("/p/<slug>/pdf")
    def paper_public_pdf(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()
        if paper_is_expired(paper):
            abort(404)
        if not _paper_visible_to_request(paper):
            abort(404)
        if not paper.pdf_path:
            abort(404)
        return render_template("pdf_reader.html", paper=paper)

    @app.route("/p/<slug>/pdf/file")
    def paper_public_pdf_file(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()
        if paper_is_expired(paper):
            abort(404)
        if not _paper_visible_to_request(paper):
            abort(404)
        if not paper.pdf_path:
            abort(404)
        pdf_name = os.path.basename(paper.pdf_path)
        ensure_local(os.path.join(app.config["PDF_FOLDER"], pdf_name), f"pdfs/{pdf_name}")
        response = send_from_directory(
            app.config["PDF_FOLDER"],
            pdf_name,
            mimetype="application/pdf",
        )
        # Inline so the iframe can render it; discourage indexing.
        response.headers["Content-Disposition"] = 'inline; filename="paper.pdf"'
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        # SEC-8: PDFs can embed JavaScript. Serve them under a locked-down CSP
        # (no scripts/plugins, only same-origin framing) set explicitly so the
        # permissive app-wide CSP/X-Frame-Options is not applied via setdefault.
        # SAMEORIGIN still allows our own pdf_reader.html iframe to render it.
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self' blob: data:; "
            "style-src 'unsafe-inline'; object-src 'none'; script-src 'none'; "
            "frame-ancestors 'self'"
        )
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @app.route("/dashboard")
    @login_required
    def dashboard():
        # PERF-2: eager-load models so the template's per-row p.models access
        # does not trigger an N+1 query storm.
        papers = (
            active_paper_query()
            .options(selectinload(Paper.models))
            .filter_by(user_id=current_user.id)
            .order_by(Paper.created_at.desc())
            .all()
        )
        return render_template("dashboard.html", papers=papers)

    @app.route("/admin", defaults={"admin_page": "overview"})
    @app.route("/admin/<admin_page>")
    @login_required
    def admin_dashboard(admin_page):
        require_admin()
        admin_pages = {
            "overview",
            "users",
            "content",
            "models",
            "access",
            "revenue",
            "security",
            "storage",
            "logs",
            "backups",
            "blog",
        }
        if admin_page not in admin_pages:
            abort(404)
        user_query_text = (request.args.get("user_q") or "").strip()
        user_plan_filter = (request.args.get("user_plan") or "all").strip().lower()
        user_role_filter = (request.args.get("user_role") or "all").strip().lower()
        model_status_filter = (request.args.get("model_status") or "all").strip().lower()
        job_status_filter = (request.args.get("job_status") or "all").strip().lower()
        audit_event_filter = (request.args.get("audit_event") or "all").strip().lower()
        audit_query_text = (request.args.get("audit_q") or "").strip()

        users_query = User.query
        if user_query_text:
            pattern = f"%{user_query_text.lower()}%"
            users_query = users_query.filter(
                or_(
                    func.lower(User.email).like(pattern),
                    func.lower(User.username).like(pattern),
                )
            )
        if user_plan_filter in USER_SELECTABLE_PLAN_KEYS:
            users_query = users_query.filter(User.plan == user_plan_filter)
        if user_role_filter == "admin":
            users_query = users_query.filter(User.is_admin.is_(True))
        elif user_role_filter == "member":
            users_query = users_query.filter(User.is_admin.is_(False))

        models_query = Model3D.query
        if model_status_filter != "all":
            models_query = models_query.filter(Model3D.processing_status == model_status_filter)

        jobs_query = ConversionJob.query
        if job_status_filter != "all":
            jobs_query = jobs_query.filter(ConversionJob.status == job_status_filter)

        audit_query = AuditLog.query
        if audit_event_filter != "all":
            audit_query = audit_query.filter(AuditLog.event_type == audit_event_filter)
        if audit_query_text:
            audit_pattern = f"%{audit_query_text.lower()}%"
            audit_query = audit_query.filter(
                or_(
                    func.lower(AuditLog.event_type).like(audit_pattern),
                    func.lower(AuditLog.resource_id).like(audit_pattern),
                    func.lower(AuditLog.ip_address).like(audit_pattern),
                )
            )

        users = users_query.order_by(User.created_at.desc()).limit(100).all()
        papers = Paper.query.order_by(Paper.created_at.desc()).limit(100).all()
        models = models_query.order_by(Model3D.created_at.desc()).limit(100).all()
        payments = Payment.query.order_by(Payment.created_at.desc()).limit(50).all()
        qr_links = QRLink.query.order_by(QRLink.created_at.desc()).limit(100).all()
        audit_logs = audit_query.order_by(AuditLog.timestamp.desc()).limit(50).all()
        jobs = jobs_query.order_by(ConversionJob.created_at.desc()).limit(50).all()
        now = datetime.now(UTC)
        last_7_days = now - timedelta(days=7)
        last_30_days = now - timedelta(days=30)
        paid_revenue = (
            db.session.query(func.coalesce(func.sum(Payment.amount_kurus), 0))
            .filter(Payment.status == "paid")
            .scalar()
            or 0
        )
        totals = {
            "users": User.query.count(),
            "admins": User.query.filter_by(is_admin=True).count(),
            "papers": Paper.query.count(),
            "public_papers": Paper.query.filter_by(is_public=True).count(),
            "models": Model3D.query.count(),
            "active_models": Model3D.query.filter(
                Model3D.processing_status.notin_(["queued", "processing", "failed"]),
                or_(Model3D.access_expires_at.is_(None), Model3D.access_expires_at >= now),
            ).count(),
            "qr_links": QRLink.query.count(),
            "payments": Payment.query.count(),
            "paid_revenue": paid_revenue,
            "pending_jobs": ConversionJob.query.filter_by(status="pending").count(),
            "failed_jobs": ConversionJob.query.filter_by(status="failed").count(),
        }
        processing_counts = {}
        for status, count in db.session.query(Model3D.processing_status, func.count(Model3D.id)).group_by(Model3D.processing_status).all():
            key = status or "ready"
            processing_counts[key] = processing_counts.get(key, 0) + count

        license_counts = {}
        for license_type, count in db.session.query(Model3D.license_type, func.count(Model3D.id)).group_by(Model3D.license_type).all():
            key = license_type or "free"
            license_counts[key] = license_counts.get(key, 0) + count

        source_format_counts = {}
        for source_format, count in db.session.query(Model3D.source_format, func.count(Model3D.id)).group_by(Model3D.source_format).all():
            key = source_format or "unknown"
            source_format_counts[key] = source_format_counts.get(key, 0) + count

        payment_counts = {}
        for status, count in db.session.query(Payment.status, func.count(Payment.id)).group_by(Payment.status).all():
            key = status or "pending"
            payment_counts[key] = payment_counts.get(key, 0) + count

        job_counts = {}
        for status, count in db.session.query(ConversionJob.status, func.count(ConversionJob.id)).group_by(ConversionJob.status).all():
            key = status or "pending"
            job_counts[key] = job_counts.get(key, 0) + count
        total_model_storage = db.session.query(func.coalesce(func.sum(Model3D.file_size), 0)).scalar() or 0
        revenue_30_days = (
            db.session.query(func.coalesce(func.sum(Payment.amount_kurus), 0))
            .filter(Payment.status == "paid", Payment.paid_at >= last_30_days)
            .scalar()
            or 0
        )
        papers_with_doi = Paper.query.filter(Paper.doi.isnot(None), Paper.doi != "").count()
        papers_with_pmid = Paper.query.filter(Paper.pmid.isnot(None), Paper.pmid != "").count()
        private_papers = max(totals["papers"] - totals["public_papers"], 0)
        papers_with_pdf = Paper.query.filter(Paper.pdf_path.isnot(None), Paper.pdf_path != "").count()
        papers_without_pdf = max(totals["papers"] - papers_with_pdf, 0)
        resolved_qr_total = AuditLog.query.filter(AuditLog.event_type == "qr_resolved").count()
        viewer_access_total = AuditLog.query.filter(AuditLog.event_type == "public_model_viewed").count()
        last_qr_resolved = QRLink.query.filter(QRLink.last_resolved_at.isnot(None)).order_by(QRLink.last_resolved_at.desc()).first()
        disabled_qr_count = QRLink.query.filter(QRLink.status != "active").count()
        # A model is "expired" only when it is not still queued/processing/failed
        # and not kept-alive as replacement_failed, and its access window has
        # lapsed (mirrors licensing.model_access_status, but in SQL).
        expired_qr_count = (
            db.session.query(func.count(QRLink.id))
            .join(Model3D, QRLink.model_id == Model3D.id)
            .filter(
                Model3D.processing_status.notin_(["queued", "processing", "failed", "replacement_failed"]),
                Model3D.access_expires_at.isnot(None),
                Model3D.access_expires_at < now,
            )
            .scalar()
        ) or 0
        # paper_is_expired() is currently always False (publications do not
        # expire), so this is 0 by definition. Kept as a named value so the
        # template/stats stay stable if expiry logic returns later.
        expired_public_papers = 0
        near_limit_models = (
            Model3D.query.filter(
                Model3D.file_size.isnot(None),
                Model3D.storage_limit_bytes.isnot(None),
                Model3D.file_size >= Model3D.storage_limit_bytes * 0.8,
            )
            .order_by(Model3D.file_size.desc())
            .limit(10)
            .all()
        )
        completed_jobs = ConversionJob.query.filter(
            ConversionJob.started_at.isnot(None),
            ConversionJob.finished_at.isnot(None),
        ).all()
        conversion_durations = [
            (job.finished_at - job.started_at).total_seconds()
            for job in completed_jobs
            if job.finished_at and job.started_at and job.finished_at >= job.started_at
        ]
        average_conversion_seconds = int(sum(conversion_durations) / len(conversion_durations)) if conversion_durations else 0
        failed_jobs = ConversionJob.query.filter_by(status="failed").order_by(ConversionJob.finished_at.desc()).limit(10).all()
        failed_format_counts: dict[str, int] = {}
        for source_format, count in (
            db.session.query(Model3D.source_format, func.count(ConversionJob.id))
            .join(Model3D, ConversionJob.model_id == Model3D.id)
            .filter(ConversionJob.status == "failed")
            .group_by(Model3D.source_format)
            .all()
        ):
            failed_format_counts[source_format or "unknown"] = count
        field_counts = {}
        for field, count in db.session.query(Paper.field, func.count(Paper.id)).group_by(Paper.field).order_by(func.count(Paper.id).desc()).limit(8).all():
            key = field or "Unspecified"
            field_counts[key] = field_counts.get(key, 0) + count
        daily_publication_trend = []
        daily_viewer_trend = []
        for offset in range(29, -1, -1):
            day_start = (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            daily_publication_trend.append(
                {
                    "label": day_label(day_start),
                    "count": Paper.query.filter(Paper.created_at >= day_start, Paper.created_at < day_end).count(),
                }
            )
            daily_viewer_trend.append(
                {
                    "label": day_label(day_start),
                    "count": AuditLog.query.filter(
                        AuditLog.event_type == "public_model_viewed",
                        AuditLog.timestamp >= day_start,
                        AuditLog.timestamp < day_end,
                    ).count(),
                }
            )
        monthly_revenue = []
        for offset in range(11, -1, -1):
            month_seed = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=offset * 31)).replace(day=1)
            next_month = (month_seed.replace(day=28) + timedelta(days=4)).replace(day=1)
            amount = (
                db.session.query(func.coalesce(func.sum(Payment.amount_kurus), 0))
                .filter(Payment.status == "paid", Payment.paid_at >= month_seed, Payment.paid_at < next_month)
                .scalar()
                or 0
            )
            monthly_revenue.append({"label": month_label(month_seed), "amount": amount})
        top_viewed_rows = (
            db.session.query(AuditLog.resource_id, func.count(AuditLog.id))
            .filter(AuditLog.event_type == "public_model_viewed", AuditLog.resource_id.isnot(None))
            .group_by(AuditLog.resource_id)
            .order_by(func.count(AuditLog.id).desc())
            .limit(10)
            .all()
        )
        viewed_model_ids = [r[0] for r in top_viewed_rows]
        viewed_models_map = {m.id: m for m in Model3D.query.filter(Model3D.id.in_(viewed_model_ids)).all()} if viewed_model_ids else {}
        top_viewed_models = [
            {"model": viewed_models_map.get(model_id), "model_id": model_id, "count": count}
            for model_id, count in top_viewed_rows
        ]
        storage_rows = (
            db.session.query(
                Model3D.user_id,
                func.coalesce(func.sum(Model3D.file_size), 0),
                func.count(Model3D.id),
            )
            .group_by(Model3D.user_id)
            .order_by(func.coalesce(func.sum(Model3D.file_size), 0).desc())
            .limit(10)
            .all()
        )
        storage_user_ids = [r[0] for r in storage_rows]
        storage_users_map = {u.id: u for u in User.query.filter(User.id.in_(storage_user_ids)).all()} if storage_user_ids else {}
        storage_by_user = [
            {"user": storage_users_map.get(user_id), "user_id": user_id, "size": total_size or 0, "models": model_count}
            for user_id, total_size, model_count in storage_rows
        ]
        # Filesystem scanning (os.walk over four folders) and orphan detection
        # are expensive, so only run them on the pages that actually display the
        # results: "overview" needs the orphan count for critical alerts, and
        # "storage" renders the full breakdown. Other pages get cheap defaults.
        orphan_counts = {"converted": 0, "pdf": 0, "qr": 0}
        storage_breakdown = {
            "uploads": {"size": 0, "files": 0},
            "models": {"size": 0, "files": 0},
            "pdfs": {"size": 0, "files": 0},
            "qr": {"size": 0, "files": 0},
        }
        if admin_page in {"overview", "storage"}:
            upload_size, upload_files = scan_folder_size(app.config["UPLOAD_FOLDER"])
            converted_size, converted_files = scan_folder_size(app.config["CONVERTED_FOLDER"])
            qr_size, qr_files = scan_folder_size(app.config["QR_FOLDER"])
            pdf_size, pdf_files = scan_folder_size(app.config["PDF_FOLDER"])
            # Only the path columns are needed for orphan detection, so query those
            # columns directly instead of hydrating full ORM objects.
            expected_pdf_files = {
                os.path.abspath(os.path.join(app.config["PDF_FOLDER"], os.path.basename(pdf_path)))
                for (pdf_path,) in db.session.query(Paper.pdf_path).filter(Paper.pdf_path.isnot(None)).all()
                if pdf_path
            }
            expected_qr_files = {
                os.path.abspath(os.path.join(app.config["QR_FOLDER"], os.path.basename(qr_code_path)))
                for (qr_code_path,) in db.session.query(Model3D.qr_code_path).filter(Model3D.qr_code_path.isnot(None)).all()
                if qr_code_path
            }
            expected_model_files = set()
            for (model_pk,) in db.session.query(Model3D.id).all():
                model_folder = os.path.abspath(os.path.join(app.config["CONVERTED_FOLDER"], model_pk))
                expected_model_files.add(os.path.join(model_folder, "model.glb"))
                expected_model_files.add(os.path.join(model_folder, "model.usdz"))
            orphan_counts = {
                "converted": count_orphan_files(app.config["CONVERTED_FOLDER"], expected_model_files),
                "pdf": count_orphan_files(app.config["PDF_FOLDER"], expected_pdf_files),
                "qr": count_orphan_files(app.config["QR_FOLDER"], expected_qr_files),
            }
            storage_breakdown = {
                "uploads": {"size": upload_size, "files": upload_files},
                "models": {"size": converted_size, "files": converted_files},
                "pdfs": {"size": pdf_size, "files": pdf_files},
                "qr": {"size": qr_size, "files": qr_files},
            }
        security_events = {
            "admin_actions": AuditLog.query.filter(AuditLog.event_type.like("admin_%")).count(),
            "account_deleted": AuditLog.query.filter_by(event_type="account_deleted").count(),
            "email_changed": AuditLog.query.filter_by(event_type="email_changed").count(),
            "password_changed": AuditLog.query.filter_by(event_type="password_changed").count(),
            "failed_logins": AuditLog.query.filter_by(event_type="user_login_failed").count(),
        }
        critical_alerts = []
        if totals["failed_jobs"]:
            critical_alerts.append(f"{totals['failed_jobs']} failed conversion job(s)")
        if near_limit_models:
            critical_alerts.append(f"{len(near_limit_models)} model(s) near storage limit")
        if expired_public_papers:
            critical_alerts.append(f"{expired_public_papers} expired public publication(s)")
        if sum(orphan_counts.values()):
            critical_alerts.append(f"{sum(orphan_counts.values())} orphan file(s) detected")
        stats = {
            "new_users_7d": User.query.filter(User.created_at >= last_7_days).count(),
            "new_users_30d": User.query.filter(User.created_at >= last_30_days).count(),
            "new_papers_7d": Paper.query.filter(Paper.created_at >= last_7_days).count(),
            "new_papers_30d": Paper.query.filter(Paper.created_at >= last_30_days).count(),
            "new_models_30d": Model3D.query.filter(Model3D.created_at >= last_30_days).count(),
            "papers_with_pdf": papers_with_pdf,
            "papers_without_pdf": papers_without_pdf,
            "papers_with_doi": papers_with_doi,
            "papers_with_pmid": papers_with_pmid,
            "private_papers": private_papers,
            "public_ratio": round((totals["public_papers"] / totals["papers"]) * 100) if totals["papers"] else 0,
            "total_model_storage": total_model_storage,
            "storage_average": int(total_model_storage / totals["models"]) if totals["models"] else 0,
            "revenue_30_days": revenue_30_days,
            "average_conversion_seconds": average_conversion_seconds,
            "failed_login_24h": AuditLog.query.filter(
                AuditLog.event_type == "user_login_failed",
                AuditLog.timestamp >= now - timedelta(hours=24),
            ).count(),
            "qr_resolved_30d": QRLink.query.filter(QRLink.last_resolved_at >= last_30_days).count(),
            "qr_resolved_total": resolved_qr_total,
            "viewer_access_total": viewer_access_total,
            "last_qr_resolved_at": last_qr_resolved.last_resolved_at if last_qr_resolved else None,
            "disabled_qr_count": disabled_qr_count,
            "expired_qr_count": expired_qr_count,
            "expired_public_papers": expired_public_papers,
        }
        largest_models = (
            Model3D.query.filter(Model3D.file_size.isnot(None))
            .order_by(Model3D.file_size.desc())
            .limit(5)
            .all()
        )
        expiring_models = (
            Model3D.query.filter(
                Model3D.access_expires_at.isnot(None),
                Model3D.access_expires_at >= now,
                Model3D.access_expires_at <= now + timedelta(days=30),
            )
            .order_by(Model3D.access_expires_at.asc())
            .limit(10)
            .all()
        )
        if admin_page == "backups":
            ensure_daily_backup(app, created_by_user_id=current_user.id)
        backups = list_backup_archives(app) if admin_page == "backups" else []
        blog_posts = (
            BlogPost.query.order_by(BlogPost.created_at.desc()).all() if admin_page == "blog" else []
        )
        editing_post = None
        if admin_page == "blog":
            edit_id = (request.args.get("edit") or "").strip()
            if edit_id.isdigit():
                editing_post = db.session.get(BlogPost, int(edit_id))
        return render_template(
            "admin_dashboard.html",
            users=users,
            papers=papers,
            models=models,
            payments=payments,
            qr_links=qr_links,
            audit_logs=audit_logs,
            jobs=jobs,
            totals=totals,
            stats=stats,
            processing_counts=processing_counts,
            license_counts=license_counts,
            source_format_counts=source_format_counts,
            payment_counts=payment_counts,
            job_counts=job_counts,
            field_counts=field_counts,
            failed_format_counts=failed_format_counts,
            failed_jobs=failed_jobs,
            largest_models=largest_models,
            expiring_models=expiring_models,
            near_limit_models=near_limit_models,
            daily_publication_trend=daily_publication_trend,
            daily_viewer_trend=daily_viewer_trend,
            monthly_revenue=monthly_revenue,
            top_viewed_models=top_viewed_models,
            storage_by_user=storage_by_user,
            storage_breakdown=storage_breakdown,
            orphan_counts=orphan_counts,
            security_events=security_events,
            critical_alerts=critical_alerts,
            backups=backups,
            blog_posts=blog_posts,
            editing_post=editing_post,
            active_page=admin_page,
            filters={
                "user_q": user_query_text,
                "user_plan": user_plan_filter,
                "user_role": user_role_filter,
                "model_status": model_status_filter,
                "job_status": job_status_filter,
                "audit_event": audit_event_filter,
                "audit_q": audit_query_text,
            },
        )

    @app.route("/admin/users/<int:user_id>")
    @login_required
    def admin_user_detail(user_id):
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        papers = active_paper_query().filter_by(user_id=user.id).order_by(Paper.created_at.desc()).all()
        models = Model3D.query.filter_by(user_id=user.id).order_by(Model3D.created_at.desc()).all()
        payments = Payment.query.filter_by(user_id=user.id).order_by(Payment.created_at.desc()).all()
        audit_events = AuditLog.query.filter_by(user_id=user.id).order_by(AuditLog.timestamp.desc()).limit(50).all()
        total_spent = sum(payment.amount_kurus for payment in payments if payment.status == "paid")
        log_audit("admin_user_detail_viewed", user_id=current_user.id, resource_id=str(user.id))
        return render_template(
            "admin_user_detail.html",
            user=user,
            papers=papers,
            models=models,
            payments=payments,
            audit_events=audit_events,
            total_spent=total_spent,
        )

    @app.route("/admin/users/<int:user_id>/dashboard")
    @login_required
    def admin_user_dashboard(user_id):
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        papers = active_paper_query().filter_by(user_id=user.id).order_by(Paper.created_at.desc()).all()
        log_audit("admin_user_dashboard_viewed", user_id=current_user.id, resource_id=str(user.id))
        return render_template(
            "dashboard.html",
            papers=papers,
            admin_view=True,
            viewed_user=user,
        )

    @app.route("/admin/backups/create", methods=["POST"])
    @login_required
    def admin_backup_create():
        require_admin()
        filename = create_backup_archive(app, created_by_user_id=current_user.id, reason="manual")
        flash(f"Backup created: {filename}", "success")
        return redirect(url_for("admin_dashboard", admin_page="backups"))

    @app.route("/admin/backups/<filename>")
    @login_required
    def admin_backup_download(filename):
        require_admin()
        safe_name = os.path.basename(filename)
        log_audit("admin_backup_downloaded", user_id=current_user.id, resource_id=safe_name)
        return send_from_directory(backup_folder(app), safe_name, as_attachment=True)

    @app.route("/admin/users/<int:user_id>/role", methods=["POST"])
    @login_required
    def admin_user_role_update(user_id):
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        make_admin = request.form.get("is_admin") == "1"
        if user.id == current_user.id and not make_admin:
            flash("You cannot remove your own admin access.", "warning")
            return redirect(url_for("admin_dashboard", admin_page="users"))
        previous = user.is_admin
        user.is_admin = make_admin
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="users"))
        log_audit(
            "admin_user_role_changed",
            user_id=current_user.id,
            resource_id=str(user.id),
            details={"from": previous, "to": make_admin},
        )
        flash(f"Admin access updated for {user.email}.", "success")
        return redirect(url_for("admin_dashboard", admin_page="users"))

    @app.route("/admin/users/<int:user_id>/plan", methods=["POST"])
    @login_required
    def admin_user_plan_update(user_id):
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        new_plan = (request.form.get("plan") or "free").strip().lower()
        if not is_valid_user_plan(new_plan):
            flash("Invalid user plan.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="users"))
        previous = user.plan
        user.plan = new_plan
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="users"))
        log_audit(
            "admin_user_plan_changed",
            user_id=current_user.id,
            resource_id=str(user.id),
            details={"from": previous, "to": new_plan},
        )
        flash(f"Plan updated for {user.email}.", "success")
        return redirect(url_for("admin_dashboard", admin_page="users"))

    @app.route("/admin/papers/<int:paper_id>/visibility", methods=["POST"])
    @login_required
    def admin_paper_visibility_update(paper_id):
        require_admin()
        paper = db.session.get(Paper, paper_id)
        if not paper:
            abort(404)
        previous = {"is_public": paper.is_public, "status": paper.status}
        paper.is_public = request.form.get("is_public") == "1"
        new_status = (request.form.get("status") or "active").strip().lower()
        if new_status not in {"active", "deleted"}:
            flash("Invalid status value.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="content"))
        paper.status = new_status
        if paper.status == "deleted":
            paper.is_public = False
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="content"))
        log_audit(
            "admin_paper_visibility_changed",
            user_id=current_user.id,
            resource_id=str(paper.id),
            details={"from": previous, "to": {"is_public": paper.is_public, "status": paper.status}},
        )
        flash(f"Publication updated: {paper.title}.", "success")
        return redirect(url_for("admin_dashboard", admin_page="content"))

    @app.route("/admin/papers/<int:paper_id>/restore", methods=["POST"])
    @login_required
    def admin_paper_restore(paper_id):
        require_admin()
        paper = db.session.get(Paper, paper_id)
        if not paper:
            abort(404)
        previous = {"status": paper.status, "deleted_at": paper.deleted_at.isoformat() if paper.deleted_at else None}
        paper.status = "active"
        paper.deleted_at = None
        paper.deleted_by_user_id = None
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="content"))
        log_audit(
            "admin_paper_restored",
            user_id=current_user.id,
            resource_id=str(paper.id),
            details={"from": previous, "to": {"status": paper.status}},
        )
        flash(f"Publication restored: {paper.title}.", "success")
        return redirect(url_for("admin_dashboard", admin_page="content"))

    @app.route("/admin/models/<model_id>/license", methods=["POST"])
    @login_required
    def admin_model_license_update(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        new_license = normalize_license_type(request.form.get("license_type"))
        previous = model.license_type
        apply_model_license_defaults(model, new_license)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="models"))
        log_audit(
            "admin_model_license_changed",
            user_id=current_user.id,
            resource_id=model.id,
            details={"from": previous, "to": new_license},
        )
        flash(f"Model license updated to {get_license_plan(new_license).label}.", "success")
        return redirect(url_for("admin_dashboard", admin_page="models"))

    @app.route("/admin/models/<model_id>/processing", methods=["POST"])
    @login_required
    def admin_model_processing_update(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        new_status = (request.form.get("processing_status") or "ready").strip().lower()
        if new_status not in {"queued", "processing", "ready", "failed", "replacement_failed"}:
            flash("Invalid model processing status.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="models"))
        previous = model.processing_status
        model.processing_status = new_status
        if new_status != "failed":
            model.processing_error = None
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="models"))
        log_audit(
            "admin_model_processing_changed",
            user_id=current_user.id,
            resource_id=model.id,
            details={"from": previous, "to": new_status},
        )
        flash("Model processing status updated.", "success")
        return redirect(url_for("admin_dashboard", admin_page="models"))

    @app.route("/admin/qr-links/<int:qr_id>/status", methods=["POST"])
    @login_required
    def admin_qr_status_update(qr_id):
        require_admin()
        qr_link = db.session.get(QRLink, qr_id)
        if not qr_link:
            abort(404)
        new_status = (request.form.get("status") or "active").strip().lower()
        if new_status not in {"active", "disabled"}:
            flash("Invalid QR status.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="access"))
        previous = qr_link.status
        qr_link.status = new_status
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="access"))
        log_audit(
            "admin_qr_status_changed",
            user_id=current_user.id,
            resource_id=qr_link.public_id,
            details={"from": previous, "to": new_status},
        )
        flash(f"QR record {qr_link.public_id} updated.", "success")
        return redirect(url_for("admin_dashboard", admin_page="access"))

    @app.route("/admin/payments/<int:payment_id>/status", methods=["POST"])
    @login_required
    def admin_payment_status_update(payment_id):
        require_admin()
        payment = db.session.get(Payment, payment_id)
        if not payment:
            abort(404)
        new_status = (request.form.get("status") or "pending").strip().lower()
        if new_status not in {"pending", "paid", "failed", "refunded"}:
            flash("Invalid payment status.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        previous = payment.status
        payment.status = new_status
        if new_status == "paid" and not payment.paid_at:
            payment.paid_at = datetime.now(UTC)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        log_audit(
            "admin_payment_status_changed",
            user_id=current_user.id,
            resource_id=str(payment.id),
            details={"from": previous, "to": new_status},
        )
        flash("Payment status updated.", "success")
        return redirect(url_for("admin_dashboard", admin_page="revenue"))

    def _read_blog_form() -> dict:
        rm = (request.form.get("read_minutes") or "").strip()
        return {
            "title": (request.form.get("title") or "").strip(),
            "description": (request.form.get("description") or "").strip() or None,
            "body": (request.form.get("body") or "").strip(),
            "tags": (request.form.get("tags") or "").strip() or None,
            "persona": (request.form.get("persona") or "").strip() or None,
            "author": (request.form.get("author") or "").strip() or "AcademicAR Team",
            "read_minutes": int(rm) if rm.isdigit() else None,
            "is_published": (request.form.get("is_published") or "").lower() in {"on", "1", "true", "yes"},
        }

    @app.route("/admin/blog/create", methods=["POST"])
    @login_required
    def admin_blog_create():
        require_admin()
        data = _read_blog_form()
        if not data["title"] or not data["body"]:
            flash("Title and body are required.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="blog"))
        post = BlogPost(
            slug=make_blog_slug(data["title"]),
            title=data["title"][:300],
            description=(data["description"] or None) and data["description"][:500],
            body=data["body"],
            tags=data["tags"],
            persona=data["persona"],
            author=data["author"][:120],
            read_minutes=data["read_minutes"],
            is_published=data["is_published"],
        )
        db.session.add(post)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not create the post. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="blog"))
        log_audit("blog_post_created", user_id=current_user.id, resource_id=str(post.id), details={"slug": post.slug})
        flash(f"Blog post '{post.title}' created.", "success")
        return redirect(url_for("admin_dashboard", admin_page="blog"))

    @app.route("/admin/blog/<int:post_id>/update", methods=["POST"])
    @login_required
    def admin_blog_update(post_id):
        require_admin()
        post = db.session.get(BlogPost, post_id)
        if not post:
            abort(404)
        data = _read_blog_form()
        if not data["title"] or not data["body"]:
            flash("Title and body are required.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="blog", edit=post_id))
        # Slug stays stable so existing links don't break.
        post.title = data["title"][:300]
        post.description = (data["description"] or None) and data["description"][:500]
        post.body = data["body"]
        post.tags = data["tags"]
        post.persona = data["persona"]
        post.author = data["author"][:120]
        post.read_minutes = data["read_minutes"]
        post.is_published = data["is_published"]
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update the post. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="blog", edit=post_id))
        log_audit("blog_post_updated", user_id=current_user.id, resource_id=str(post.id), details={"slug": post.slug})
        flash("Blog post updated.", "success")
        return redirect(url_for("admin_dashboard", admin_page="blog"))

    @app.route("/admin/blog/<int:post_id>/publish", methods=["POST"])
    @login_required
    def admin_blog_publish(post_id):
        require_admin()
        post = db.session.get(BlogPost, post_id)
        if not post:
            abort(404)
        post.is_published = not post.is_published
        db.session.commit()
        log_audit(
            "blog_post_publish_toggled",
            user_id=current_user.id,
            resource_id=str(post.id),
            details={"is_published": post.is_published},
        )
        flash("Post published." if post.is_published else "Post moved to drafts.", "success")
        return redirect(url_for("admin_dashboard", admin_page="blog"))

    @app.route("/admin/blog/<int:post_id>/delete", methods=["POST"])
    @login_required
    def admin_blog_delete(post_id):
        require_admin()
        post = db.session.get(BlogPost, post_id)
        if not post:
            abort(404)
        slug = post.slug
        db.session.delete(post)
        db.session.commit()
        log_audit("blog_post_deleted", user_id=current_user.id, resource_id=str(post_id), details={"slug": slug})
        flash("Blog post deleted.", "success")
        return redirect(url_for("admin_dashboard", admin_page="blog"))

    @app.route("/admin/blog/upload-image", methods=["POST"])
    @login_required
    @limiter.limit("60 per hour", methods=["POST"])
    def admin_blog_upload_image():
        """Admin-only inline image upload for blog posts. Returns JSON with a
        same-origin URL + a ready-to-paste Markdown snippet. Stored locally and
        mirrored to R2 like every other asset."""
        require_admin()
        file = request.files.get("image")
        if not file or not file.filename:
            return jsonify({"error": "No image was provided."}), 400
        ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
        if ext not in ALLOWED_BLOG_IMAGE_EXTENSIONS:
            return jsonify({"error": "Unsupported type. Use PNG, JPG, WEBP, or GIF."}), 400
        safe = secure_filename(file.filename) or f"image.{ext}"
        filename = f"{uuid.uuid4().hex[:12]}_{safe}"
        dest = os.path.join(app.config["BLOG_IMAGE_FOLDER"], filename)
        try:
            safe_save_file(file, dest)
        except StorageError:
            return jsonify({"error": "Could not save the image. Please try again."}), 500
        try:
            if os.path.getsize(dest) > MAX_BLOG_IMAGE_BYTES:
                os.remove(dest)
                return jsonify({"error": "Image is too large (max 10 MB)."}), 400
        except OSError:
            pass
        mirror_file(dest, f"blog_images/{filename}")
        url = url_for("serve_blog_image", filename=filename)
        log_audit("blog_image_uploaded", user_id=current_user.id, details={"filename": filename})
        return jsonify({"url": url, "markdown": f"![]({url})"})

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        if request.method == "POST":
            new_plan = (request.form.get("plan") or "").strip().lower()
            if not is_valid_user_plan(new_plan):
                flash("Invalid plan choice.", "danger")
                return redirect(url_for("profile"))
            previous = current_user.plan or "free"
            if new_plan == previous:
                flash("You're already on this plan.", "info")
                return redirect(url_for("profile"))

            # Guard the development-only "instant paid upgrade". Without a real
            # payment gateway we must never grant paid plans for free in
            # production (see Config.ALLOW_DEV_PAYMENTS).
            paid_plans = {"academic", "extended_archive"}
            if new_plan in paid_plans and not current_app.config.get("ALLOW_DEV_PAYMENTS", False):
                flash(
                    "Paid plans are not available yet — online payment is not "
                    "configured. Please contact support to upgrade your account.",
                    "warning",
                )
                return redirect(url_for("profile"))

            current_user.plan = new_plan
            try:
                payment = None
                if new_plan in {"academic", "extended_archive"}:
                    amount_kurus = 50000 if new_plan == "academic" else 125000
                    payment = Payment(
                        user_id=current_user.id,
                        amount_kurus=amount_kurus,
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
                        "You're now on the Academic plan. New models you upload get 3-year AR & QR access, no watermark, and a persistent viewer URL. Existing models keep their current license until you upgrade them.",
                        "success",
                    )
                elif new_plan == "extended_archive":
                    flash(
                        "You're now on the Extended Archive plan. New models you upload get 10-year AR & QR access, priority archival storage, and rich metadata fields. Existing models keep their current license until you upgrade them.",
                        "success",
                    )
                else:
                    flash(
                        "Switched to the Free plan. New models you upload get 3-day AR & QR access (watermarked). Existing models keep their current license.",
                        "info",
                    )
            except SQLAlchemyError:
                db.session.rollback()
                flash("Could not update plan. Please try again.", "danger")
            return redirect(url_for("profile"))

        # Profile statistics. PERF-2: eager-load models to avoid N+1 in the
        # Python aggregation loops below.
        user_papers = (
            Paper.query.options(selectinload(Paper.models))
            .filter_by(user_id=current_user.id)
            .all()
        )
        paper_count = len(user_papers)
        # BUG-1: plans live at the model level (license_type drives real AR/QR
        # access), so report the model license distribution rather than the
        # legacy paper.package_type which is always "model_based".
        user_models = [m for p in user_papers for m in p.models]
        free_model_count = sum(1 for m in user_models if (m.license_type or "free") == "free")
        academic_model_count = sum(1 for m in user_models if m.license_type == "academic")
        extended_model_count = sum(1 for m in user_models if m.license_type == "extended_archive")
        public_paper_count = sum(1 for p in user_papers if p.is_public)
        private_paper_count = paper_count - public_paper_count
        pdf_paper_count = sum(1 for p in user_papers if p.pdf_path)
        model_count = len(user_models)
        recent_payments = (
            Payment.query.filter_by(user_id=current_user.id)
            .order_by(Payment.created_at.desc())
            .limit(5)
            .all()
        )
        # BUG-3: real count of models whose AR/QR access expires within 7 days.
        soon = datetime.now(UTC) + timedelta(days=7)
        expiring_soon = 0
        for m in user_models:
            exp = m.access_expires_at
            if not exp or is_access_expired(exp):
                continue
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if exp <= soon:
                expiring_soon += 1

        return render_template(
            "profile.html",
            user=current_user,
            paper_count=paper_count,
            free_model_count=free_model_count,
            academic_model_count=academic_model_count,
            extended_model_count=extended_model_count,
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
    @limiter.limit("5 per hour", methods=["POST"])
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
        max_length = 1024
        if len(new_pw) < min_length:
            flash(f"New password must be at least {min_length} characters.", "danger")
            return redirect(url_for("profile"))
        if len(new_pw) > max_length:
            flash(f"Password must be at most {max_length} characters.", "danger")
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
    @limiter.limit("5 per hour", methods=["POST"])
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

        # SEC-5: never switch the email until the new address is verified. Send
        # a signed, time-limited confirmation link to the NEW address; the email
        # only changes when that link is opened (see account_confirm_email).
        from itsdangerous import URLSafeTimedSerializer

        serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="email-change")
        token = serializer.dumps({"uid": current_user.id, "new_email": new_email})
        confirm_url = url_for("account_confirm_email", token=token, _external=True)
        from utils.email import send_email

        send_email(
            new_email,
            "Confirm your new AcademicAR email address",
            (
                "We received a request to change the email address on your "
                "AcademicAR account.\n\n"
                f"To confirm {new_email}, open this link within 1 hour:\n"
                f"{confirm_url}\n\n"
                "If you did not request this change, you can ignore this email; "
                "your current address stays active."
            ),
        )
        log_audit(
            "email_change_requested",
            user_id=current_user.id,
            details={"from": current_user.email, "to": new_email},
        )
        flash(
            f"We sent a confirmation link to {new_email}. Open it to finish "
            "changing your email. Your current address stays active until then.",
            "info",
        )
        return redirect(url_for("profile"))

    @app.route("/account/email/confirm/<token>")
    @login_required
    def account_confirm_email(token):
        from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

        serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="email-change")
        try:
            data = serializer.loads(token, max_age=3600)
        except SignatureExpired:
            flash("This confirmation link has expired. Please request the change again.", "warning")
            return redirect(url_for("profile"))
        except BadSignature:
            abort(404)

        if data.get("uid") != current_user.id:
            abort(403)
        new_email = (data.get("new_email") or "").strip().lower()
        if not new_email or "@" not in new_email:
            abort(400)
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
            flash("Email address confirmed and updated.", "success")
        except (IntegrityError, SQLAlchemyError):
            db.session.rollback()
            flash("Could not update email. Please try again.", "danger")
        return redirect(url_for("profile"))

    @app.route("/account/profile", methods=["POST"])
    @login_required
    @limiter.limit("10 per hour", methods=["POST"])
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
    @limiter.limit("3 per hour", methods=["POST"])
    def account_delete():
        confirm = (request.form.get("confirm") or "").strip()
        password = request.form.get("current_password") or ""
        if confirm != "DELETE":
            flash('Type DELETE in the confirmation box to proceed.', "danger")
            return redirect(url_for("profile"))
        if current_user.password_hash and not current_user.check_password(password):
            flash("Current password is incorrect.", "danger")
            return redirect(url_for("profile"))

        files_to_remove = []
        for paper in current_user.papers:
            files_to_remove.extend(collect_paper_file_paths(app, paper))

        user_id = current_user.id
        user_email = current_user.email
        try:
            log_audit("account_deleted", user_id=user_id, details={"email": user_email})
            ConversionJob.query.filter_by(user_id=user_id).update({"user_id": None})
            Payment.query.filter_by(user_id=user_id).update({"user_id": None})
            AuditLog.query.filter(AuditLog.user_id == user_id, AuditLog.event_type != "account_deleted").update({"user_id": None})
            Paper.query.filter_by(deleted_by_user_id=user_id).update({"deleted_by_user_id": None})
            db.session.delete(current_user)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Account deletion failed")
            flash("Could not delete account. Please try again.", "danger")
            return redirect(url_for("profile"))

        cleanup_paths(files_to_remove)
        from flask_login import logout_user
        logout_user()
        flash("Your account and all associated data were permanently deleted.", "info")
        return redirect(url_for("landing"))

    @app.route("/papers/fetch-metadata", methods=["POST"])
    @login_required
    @limiter.limit("30 per hour", methods=["POST"])
    @limiter.limit("6 per minute", methods=["POST"])
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
            headers = {"User-Agent": f"AcademicAR/1.0 (mailto:{current_app.config.get('CONTACT_EMAIL', 'info@academicar.com')})"}
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
            req = urllib.request.Request(url, headers={"User-Agent": f"AcademicAR/1.0 (mailto:{current_app.config.get('CONTACT_EMAIL', 'info@academicar.com')})"})
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
                    color=request.form.get("color") if request.form.get("color_enabled") == "yes" else None,
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
        paper = active_paper_query().filter_by(slug=slug).first_or_404()
        if paper.user_id != current_user.id:
            abort(403)
        return render_template("paper_detail.html", paper=paper)

    @app.route("/papers/<slug>/edit", methods=["GET", "POST"])
    @login_required
    @require_paper_ownership
    def paper_edit(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()

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
            paper.expires_at = None

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
            elif request.form.get("delete_pdf") == "1":
                if paper.pdf_path:
                    old_pdf_path = os.path.join(app.config["PDF_FOLDER"], os.path.basename(paper.pdf_path))
                    paper.pdf_path = None

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

    @app.route("/papers/<slug>/upload-pdf", methods=["POST"])
    @login_required
    @require_paper_ownership
    def paper_upload_pdf(slug):
        paper = active_paper_query().filter_by(slug=slug).first()
        if not paper:
            return jsonify({"success": False, "error": "Publication not found"}), 404
            
        pdf_file = request.files.get("pdf")
        if not pdf_file or not pdf_file.filename:
            return jsonify({"success": False, "error": "No file uploaded"}), 400
            
        if not allowed_pdf(pdf_file.filename):
            return jsonify({"success": False, "error": "Only .pdf files are accepted"}), 400
            
        pdf_filename = f"{uuid.uuid4()}_{secure_filename(pdf_file.filename)}"
        saved_pdf_path = os.path.join(app.config["PDF_FOLDER"], pdf_filename)
        
        try:
            safe_save_file(pdf_file, saved_pdf_path)
        except StorageError as e:
            return jsonify({"success": False, "error": str(e)}), 500
            
        pdf_errors = validate_pdf_file(saved_pdf_path)
        if pdf_errors:
            cleanup_file(saved_pdf_path)
            return jsonify({"success": False, "error": "Invalid PDF file: " + "; ".join(pdf_errors)}), 400
            
        old_pdf_path = None
        if paper.pdf_path:
            old_pdf_path = os.path.join(app.config["PDF_FOLDER"], os.path.basename(paper.pdf_path))
            
        try:
            paper.pdf_path = pdf_filename
            db.session.commit()
            log_audit("paper_pdf_uploaded", user_id=current_user.id, resource_id=str(paper.id))
            mirror_file(saved_pdf_path, f"pdfs/{pdf_filename}")
        except SQLAlchemyError:
            db.session.rollback()
            cleanup_file(saved_pdf_path)
            logger.exception("Ajax PDF upload failed")
            return jsonify({"success": False, "error": "Database error while updating publication"}), 500

        cleanup_file(old_pdf_path)
        return jsonify({"success": True, "pdf_url": url_for("paper_public_pdf", slug=paper.slug)})

    @app.route("/papers/<slug>/delete-pdf", methods=["POST"])
    @login_required
    @require_paper_ownership
    def paper_delete_pdf_ajax(slug):
        paper = active_paper_query().filter_by(slug=slug).first()
        if not paper:
            return jsonify({"success": False, "error": "Publication not found"}), 404

        if not paper.pdf_path:
            return jsonify({"success": False, "error": "No PDF attached to this publication"}), 400

        old_pdf_path = os.path.join(app.config["PDF_FOLDER"], os.path.basename(paper.pdf_path))
        try:
            paper.pdf_path = None
            db.session.commit()
            cleanup_file(old_pdf_path)
            log_audit("paper_pdf_deleted", user_id=current_user.id, resource_id=str(paper.id))
            return jsonify({"success": True})
        except Exception as e:
            db.session.rollback()
            logger.exception("Paper PDF deletion failed")
            return jsonify({"success": False, "error": "Could not delete PDF"}), 500

    @app.route("/papers/<slug>/delete", methods=["POST"])
    @login_required
    @require_paper_ownership
    def paper_delete(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()
        paper_id = paper.id
        try:
            paper.status = "deleted"
            paper.is_public = False
            paper.deleted_at = datetime.now(UTC)
            paper.deleted_by_user_id = current_user.id
            db.session.commit()
            log_audit(
                "paper_deleted",
                user_id=current_user.id,
                resource_id=str(paper_id),
                details={"soft_deleted": True, "title": paper.title},
            )
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Paper delete failed")
            flash("The publication could not be deleted. Please try again.", "danger")
            return redirect(url_for("paper_detail", slug=slug))
        flash("Publication deleted.", "info")
        return redirect(url_for("dashboard"))

    @app.route("/papers/<slug>/upload-model", methods=["POST"])
    @login_required
    @limiter.limit(upload_rate_limit_value, methods=["POST"], exempt_when=upload_rate_limit_disabled)
    @require_paper_ownership
    def upload_model(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()
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
            color=request.form.get("color") if request.form.get("color_enabled") == "yes" else None,
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
        model.replaced_at = datetime.now(UTC)
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
        # Return to wherever the form was submitted from (e.g. the paper's model
        # registry) when a safe same-site path is provided, else the model page.
        next_url = request.form.get("next") or ""
        dest = next_url if (next_url.startswith("/") and not next_url.startswith("//")) else url_for("model_edit", model_id=model.id)
        color_command = request.form.get("color_command")
        color_input = (request.form.get("color") or "").strip() or None
        # text command takes precedence so users can type "make it light gray".
        parsed = color_from_command(color_command) if color_command else None
        new_color = parsed or color_input
        if not new_color or HEX_COLOR_PATTERN.fullmatch(new_color) is None:
            flash("Provide a valid hex color (#RRGGBB) or a known color name.", "danger")
            return redirect(dest)

        rgba = hex_to_rgba(new_color)
        if rgba is None:
            flash("Invalid color value.", "danger")
            return redirect(dest)

        roughness_raw = request.form.get("roughness")
        metallic_raw = request.form.get("metallic")
        roughness = max(0.0, min(1.0, float(roughness_raw))) if roughness_raw else (model.appearance_roughness or 0.35)
        metallic = max(0.0, min(1.0, float(metallic_raw))) if metallic_raw else (model.appearance_metallic or 0.05)

        ar_placement_raw = request.form.get("ar_placement")
        ar_placement = ar_placement_raw if ar_placement_raw in ("floor", "wall") else (model.ar_placement or "floor")

        glb_path = model.glb_path
        # Restore the working GLB from the R2 mirror if the local copy is gone.
        ensure_local(glb_path, f"converted/{model.id}/model.glb")
        backup_path = glb_path + APPEARANCE_BACKUP_SUFFIX
        try:
            if os.path.exists(glb_path):
                shutil.copy2(glb_path, backup_path)
            try:
                enrich_glb_for_ar(glb_path, rgba, roughness=roughness, metallic=metallic)
            except Exception as exc:
                logger.exception("Appearance enrichment failed; restoring backup")
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, glb_path)
                glb_exists = os.path.exists(glb_path)
                detail = f" (GLB {'found' if glb_exists else 'NOT found'} at {glb_path}; {type(exc).__name__}: {exc})"
                flash(f"The model appearance could not be updated. The previous version is still active.{detail}", "warning")
                return redirect(dest)

            model.appearance_color = new_color
            model.appearance_roughness = roughness
            model.appearance_metallic = metallic
            model.ar_placement = ar_placement
            try:
                model.file_size = os.path.getsize(glb_path)
            except OSError:
                pass
            db.session.commit()
            # Re-mirror the rewritten GLB so R2 doesn't keep the pre-recolor copy.
            mirror_file(glb_path, f"converted/{model.id}/model.glb")
            log_audit(
                "model_appearance_updated",
                user_id=current_user.id,
                resource_id=model_id,
                details={"color": new_color, "roughness": roughness, "metallic": metallic, "ar_placement": ar_placement},
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
        return redirect(dest)

    @app.route("/models/<model_id>/rescale", methods=["POST"])
    @login_required
    @require_model_ownership
    def model_rescale(model_id):
        """Rescale the GLB so its longest dimension matches the user-specified cm value."""
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        target_cm_raw = request.form.get("target_longest_cm", "").strip()
        try:
            target_cm = float(target_cm_raw)
        except (ValueError, TypeError):
            flash("Enter a valid number for the target dimension.", "danger")
            return redirect(url_for("model_edit", model_id=model.id))
        if target_cm <= 0 or target_cm > 500:
            flash("Target dimension must be between 0 and 500 cm.", "danger")
            return redirect(url_for("model_edit", model_id=model.id))

        glb_path = model.glb_path
        ensure_local(glb_path, f"converted/{model_id}/model.glb")
        backup_path = glb_path + ".rescale.bak"
        try:
            if os.path.exists(glb_path):
                shutil.copy2(glb_path, backup_path)
            try:
                from pygltflib import GLTF2
                import numpy as _np
                gltf = GLTF2.load(glb_path)
                import trimesh as _trimesh
                loaded = _trimesh.load(glb_path, force="scene")
                bounds = loaded.bounds
                current_extent_m = float(_np.ptp(bounds, axis=0).max())
                if current_extent_m <= 0:
                    raise ValueError("Cannot measure model extent")
                target_m = target_cm / 100.0
                scale_factor = target_m / current_extent_m
                for node in gltf.nodes or []:
                    if node.scale:
                        node.scale = [s * scale_factor for s in node.scale]
                    else:
                        node.scale = [scale_factor, scale_factor, scale_factor]
                gltf.save(glb_path)
            except Exception:
                logger.exception("Rescale failed; restoring backup")
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, glb_path)
                flash("Rescale failed. The previous model is still active.", "warning")
                return redirect(url_for("model_edit", model_id=model.id))

            model.dimensions_cm = compute_glb_dimensions_cm(glb_path)
            poster_png = os.path.join(os.path.dirname(glb_path), "poster.png")
            if generate_poster(glb_path, poster_png):
                model.poster_path = poster_png
            db.session.commit()
            # Re-mirror the rescaled GLB (and refreshed poster) to R2.
            mirror_file(glb_path, f"converted/{model_id}/model.glb")
            if model.poster_path:
                mirror_file(poster_png, f"converted/{model_id}/poster.png")
            log_audit(
                "model_rescaled",
                user_id=current_user.id,
                resource_id=model_id,
                details={"target_cm": target_cm},
            )
            flash(f"Model rescaled to {target_cm} cm (longest dimension).", "success")
        except SQLAlchemyError:
            db.session.rollback()
            if os.path.exists(backup_path):
                try:
                    shutil.copy2(backup_path, glb_path)
                except OSError:
                    logger.exception("Failed to restore rescale backup after DB error")
            flash("Rescale could not be saved.", "danger")
        finally:
            if os.path.exists(backup_path):
                cleanup_file(backup_path)
        return redirect(url_for("model_edit", model_id=model.id))

    @app.route("/models/<model_id>/annotations", methods=["GET"])
    def model_annotations_list(model_id):
        """Public JSON endpoint: returns annotations for a model."""
        if not is_uuid(model_id):
            abort(404)
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        if not _paper_visible_to_request(model.paper) or not model_is_accessible(model):
            abort(404)
        annotations = ModelAnnotation.query.filter_by(model_id=model_id).order_by(ModelAnnotation.order_index).all()
        return jsonify([a.to_dict() for a in annotations])

    @app.route("/models/<model_id>/annotations", methods=["POST"])
    @login_required
    @require_model_ownership
    def model_annotation_add(model_id):
        """Add a single annotation to the model."""
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        data = request.get_json(silent=True) or {}
        label = (data.get("label") or "").strip()
        if not label or len(label) > 120:
            return jsonify({"error": "Label is required (max 120 chars)"}), 400
        position = data.get("position", [0, 0, 0])
        normal = data.get("normal", [0, 1, 0])
        if not (isinstance(position, list) and len(position) == 3):
            return jsonify({"error": "Position must be [x, y, z]"}), 400
        if not (isinstance(normal, list) and len(normal) == 3):
            return jsonify({"error": "Normal must be [x, y, z]"}), 400
        max_order = db.session.query(func.max(ModelAnnotation.order_index)).filter_by(model_id=model_id).scalar() or 0
        try:
            annotation = ModelAnnotation(
                model_id=model_id,
                position_x=float(position[0]),
                position_y=float(position[1]),
                position_z=float(position[2]),
                normal_x=float(normal[0]),
                normal_y=float(normal[1]),
                normal_z=float(normal[2]),
                label=label,
                description=(data.get("description") or "").strip()[:2000] or None,
                order_index=max_order + 1,
            )
            db.session.add(annotation)
            db.session.commit()
            return jsonify(annotation.to_dict()), 201
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify({"error": "Could not save annotation"}), 500

    @app.route("/models/<model_id>/annotations/<int:annotation_id>", methods=["DELETE"])
    @login_required
    @require_model_ownership
    def model_annotation_delete(model_id, annotation_id):
        """Delete a single annotation."""
        annotation = db.session.get(ModelAnnotation, annotation_id)
        if not annotation or annotation.model_id != model_id:
            abort(404)
        try:
            db.session.delete(annotation)
            db.session.commit()
            return jsonify({"ok": True})
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify({"error": "Could not delete annotation"}), 500

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
            model.display_name = (request.form.get("display_name") or "").strip()[:255] or None
            model.description = (request.form.get("description") or "").strip()[:5000] or None
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
        if not model or not model.paper:
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
        mirror_delete(f"converted/{model_id}/model.glb")
        mirror_delete(f"converted/{model_id}/model.usdz")
        mirror_delete(f"converted/{model_id}/poster.png")
        mirror_delete(f"qr_codes/qr_{model_id}.png")
        flash("Model deleted.", "info")
        return redirect(url_for("paper_detail", slug=slug))


app = create_app()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=app.config.get("DEBUG", False),
    )
