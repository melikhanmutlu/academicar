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

from flask import Flask, Response, abort, current_app, flash, g, jsonify, make_response, redirect, render_template, request, send_from_directory, session, url_for
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
from converters.glb_quality import (
    GLBQualityError,
    apply_pbr_factors,
    bake_base_color_factor,
    embed_external_textures,
    ensure_pbr_materials,
    has_base_color_textures,
    mask_cutout_textures,
    repair_transparent_base_color,
    validate_glb_quality,
)
from converters.glb_optimize import normalize_specular_glossiness, optimize_glb
from converters.glb_scale import clamp_oversized_glb
from converters.poster import generate_poster
from converters.stl_converter import convert_glb_to_usdz, enrich_glb_for_ar
from licensing import (
    USER_SELECTABLE_PLAN_KEYS,
    apply_model_license_defaults,
    get_license_plan,
    get_license_plans,
    is_access_expired,
    license_expires_at,
    model_access_status,
    model_file_limit_error,
    model_is_accessible,
    model_upgrade_options,
    normalize_license_type,
    refresh_license_plan_cache,
)
from blog_content import code_post_slugs, get_all_posts, get_post, render_body
from discipline_content import all_disciplines, discipline_slugs, get_discipline, related_disciplines
from institution_panel import institution_bp
from institutions import (
    apply_institutional_license,
    end_institution_access_now,
    get_active_membership,
    institution_can_fund_upload,
    institution_usage,
    reapply_model_license,
    renew_institution_contract,
)
from models import AnalyticsEvent, AuditLog, BlogPost, ConversionJob, Institution, InstitutionInvite, InstitutionMember, LicensePlanConfig, Model3D, ModelAnnotation, ModelVersion, Paper, Payment, QRLink, User, db
from services.r2_mirror import mirror_file, mirror_directory, mirror_directory_sync, mirror_delete, ensure_local
from payments import (
    PAID_PLAN_KEYS,
    ForexRateUnavailable,
    apply_successful_payment,
    get_payment_provider,
    plan_amount_minor_units,
)
from url_helpers import public_url
from analytics import ALLOWED_BROWSER_EVENTS, analytics_snapshot, apply_analytics_cookie, track_event
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
        # PayTR hosted checkout: the upgrade form POSTs to our own route, which
        # 302-redirects to https://www.paytr.com/odeme/... — browsers apply
        # form-action to that redirect target, so PayTR must be allowlisted or
        # the payment redirect is blocked.
        "form-action 'self' https://www.paytr.com",
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
        user = db.session.get(User, int(user_id))
        # Deactivated accounts must not resolve to a live session, so an admin
        # deactivation takes effect immediately for already-logged-in users too.
        if user is not None and user.deactivated_at is not None:
            return None
        return user

    init_oauth(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(institution_bp)

    @app.context_processor
    def inject_globals():
        return {
            "current_year": datetime.now(UTC).year,
            "asset_version": app.config.get("ASSET_VERSION", ""),
            "format_file_size": format_file_size,
            "public_url": public_url,
            "canonical_url": canonical_url,
            "model_asset_token": model_asset_token,
            "license_plans": get_license_plans(),
            "get_license_plan": get_license_plan,
            "model_resolver_url": model_resolver_url,
            "model_access_status": model_access_status,
            "model_upgrade_options": model_upgrade_options,
            "user_selectable_plan_keys": USER_SELECTABLE_PLAN_KEYS,
            "format_model_dimensions_cm": format_model_dimensions_cm,
            "academic_fields": ACADEMIC_FIELDS,
            "project_types": PROJECT_TYPES,
            "project_workflow_stages": PROJECT_WORKFLOW_STAGES,
            "project_visibility": project_visibility,
            "admin_chip_class": admin_chip_class,
            "user_is_configured_admin": user_is_configured_admin,
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
        return apply_analytics_cookie(response)

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
        seed_license_plans(app)
        seed_builtin_blog_posts(app)
        stamp_alembic_version_if_needed(app)

    return app


# Canonical academic field/discipline options for the publication form's
# "Field" dropdown. Single source of truth for both the template (rendered via
# inject_globals) and server-side validation in validate_paper_form(). Distinct
# from discipline_content.DISCIPLINES, which is a separate, marketing-oriented
# taxonomy powering the /ar-for-* SEO landing pages (who the AR feature serves,
# not what academic field a publication belongs to) — spelling of overlapping
# terms (Biology, Archaeology, Chemistry, Engineering) is kept consistent
# between the two, but the lists are not merged into one.
ACADEMIC_FIELDS = (
    "Medicine", "Dentistry", "Engineering", "Architecture", "Biology",
    "Archaeology", "Veterinary Medicine", "Chemistry", "Other",
)

PROJECT_TYPES = (
    "research_project", "publication", "thesis", "teaching_training",
    "engineering_design", "collection_archive", "other",
)
PROJECT_WORKFLOW_STAGES = ("in_progress", "ready_for_review", "published", "archived")
PROJECT_VISIBILITIES = ("private", "unlisted", "public")

SUPPORTED_MODEL_EXTENSIONS = {"stl", "glb", "obj", "fbx"}
COMPANION_FILE_EXTENSIONS = {".mtl", ".png", ".jpg", ".jpeg", ".webp"}
# Blog post inline images (admin upload). SVG is excluded on purpose (it can
# carry script). Raster formats only.
ALLOWED_BLOG_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_BLOG_IMAGE_BYTES = 10 * 1024 * 1024
# Institution showcase logos: raster only (no SVG — script injection risk).
ALLOWED_INSTITUTION_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_INSTITUTION_LOGO_BYTES = 2 * 1024 * 1024
# Rows per page for paginated admin tables (users, papers, models, payments,
# QR records, audit log, conversion jobs).
ADMIN_PER_PAGE = 50
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


_ADMIN_CHIP_GOOD = {"ready", "active", "public", "paid", "completed", "admin"}
_ADMIN_CHIP_WARN = {"queued", "pending", "processing"}
_ADMIN_CHIP_BAD = {"failed", "replacement_failed", "expired", "cancelled", "refunded", "private", "deleted", "disabled", "suspended"}


def admin_chip_class(value) -> str:
    """Map an admin status/label string to a status-chip color modifier."""
    key = (value or "").strip().lower()
    if key in _ADMIN_CHIP_GOOD:
        return "is-ready"
    if key in _ADMIN_CHIP_WARN:
        return "is-queued"
    if key in _ADMIN_CHIP_BAD:
        return "is-failed"
    return ""


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


def seed_license_plans(app: Flask) -> None:
    """Idempotent: insert a license_plans row for any Python-fallback key that
    doesn't have one yet. Never touches a key that already has a row, so an
    admin's prior edits survive every restart. Mirrors sync_configured_admins.
    """
    from licensing import default_license_plans, refresh_license_plan_cache

    try:
        existing = {k for (k,) in db.session.query(LicensePlanConfig.key).all()}
        to_insert = [
            LicensePlanConfig(
                key=plan.key,
                label=plan.label,
                price_usd_cents=int(round(plan.price_usd * 100)),
                duration_days=plan.duration_days,
                storage_limit_bytes=plan.storage_limit_bytes,
                is_purchasable=plan.is_purchasable,
            )
            for key, plan in default_license_plans().items()
            if key not in existing
        ]
        if to_insert:
            db.session.add_all(to_insert)
            db.session.commit()
            logger.info("Seeded %s license plan row(s): %s", len(to_insert), [p.key for p in to_insert])
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not seed license plan rows")
    refresh_license_plan_cache()


def seed_builtin_blog_posts(app: Flask) -> None:
    """Idempotent: insert a blog_posts row for any built-in code post (see
    blog_content.py) whose slug has no DB row yet, so the launch articles are
    editable from the admin panel like any other post. Never touches a slug
    that already has a row, so an admin's edits (or a deliberate delete)
    survive every restart — mirrors seed_license_plans.
    """
    try:
        existing = {slug for (slug,) in db.session.query(BlogPost.slug).all()}
        to_insert = [
            BlogPost(
                slug=p["slug"],
                title=p["title"],
                description=p.get("description"),
                body=p["body"],
                tags=", ".join(p.get("tags") or []) or None,
                persona=p.get("persona"),
                author=p.get("author") or "AcademicAR Team",
                read_minutes=p.get("read_minutes"),
                is_published=True,
                created_at=datetime.strptime(p["date"], "%Y-%m-%d").replace(tzinfo=UTC),
                updated_at=datetime.strptime(p["date"], "%Y-%m-%d").replace(tzinfo=UTC),
            )
            for p in get_all_posts()
            if p["slug"] not in existing
        ]
        if to_insert:
            db.session.add_all(to_insert)
            db.session.commit()
            logger.info("Seeded %s built-in blog post row(s): %s", len(to_insert), [p.slug for p in to_insert])
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Could not seed built-in blog post rows")


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


def _describe_cli_resolution(command: list[str]) -> dict:
    """Side-effect-free presence check for a converter CLI: does the resolved
    command point at a local file, or would it fall through to an on-demand
    `npx` fetch? Never executes the command, so this can never hang or make a
    network call — unlike an actual `--version` invocation would if the
    package isn't cached locally.
    """
    if not command:
        return {"available": False, "detail": "not configured"}
    if command[0] == "npx":
        return {"available": False, "detail": f"npx fallback (not installed locally): {' '.join(command)}"}
    target = command[-1]
    available = os.path.isfile(target) or bool(shutil.which(command[0]))
    return {"available": available, "detail": " ".join(command)}


def _admin_system_health() -> dict:
    """Read-only, side-effect-free dependency + worker-liveness snapshot for
    the admin System Health page. Deliberately does not run any subprocess
    (unlike /admin/ar-doctor's live GLB->USDZ test) so this is safe and fast
    to compute on every page visit.
    """
    from converters.external_converter import FBXConverter, OBJConverter
    from converters.glb_optimize import _find_cli as find_gltf_transform_cli

    blender_path = shutil.which("blender")
    oldest_pending = (
        ConversionJob.query.filter_by(status="pending").order_by(ConversionJob.created_at.asc()).first()
    )
    oldest_pending_age_seconds = None
    if oldest_pending and oldest_pending.created_at:
        oldest_pending_age_seconds = int(
            (datetime.now(UTC) - oldest_pending.created_at.replace(tzinfo=UTC)).total_seconds()
        )
    completed_recent = (
        ConversionJob.query.filter(
            ConversionJob.started_at.isnot(None), ConversionJob.finished_at.isnot(None)
        )
        .order_by(ConversionJob.finished_at.desc())
        .limit(20)
        .all()
    )
    durations = [
        (job.finished_at - job.started_at).total_seconds()
        for job in completed_recent
        if job.finished_at and job.started_at and job.finished_at >= job.started_at
    ]
    avg_recent_seconds = int(sum(durations) / len(durations)) if durations else 0
    return {
        "blender": {"available": bool(blender_path), "detail": blender_path or "not on PATH"},
        "gltf_transform": _describe_cli_resolution(find_gltf_transform_cli()),
        "obj2gltf": _describe_cli_resolution(OBJConverter()._command()),
        "fbx2gltf": _describe_cli_resolution(FBXConverter()._command()),
        "oldest_pending_age_seconds": oldest_pending_age_seconds,
        "avg_recent_job_seconds": avg_recent_seconds,
        "pending_job_count": ConversionJob.query.filter_by(status="pending").count(),
    }


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
        user_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(users)")).fetchall()}
        if user_columns and "deactivated_at" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN deactivated_at DATETIME"))
        if model_columns and "r2_mirror_failed_at" not in model_columns:
            connection.execute(text("ALTER TABLE models ADD COLUMN r2_mirror_failed_at DATETIME"))


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


def make_institution_slug(name: str) -> str:
    """Unique institution showcase slug (/i/<slug>) from the name."""
    base = slugify(name)[:200] or "institution"
    slug = base
    counter = 1
    while Institution.query.filter_by(slug=slug).first() is not None:
        counter += 1
        if counter > 10000:
            slug = f"{base}-{uuid.uuid4().hex[:8]}"
            break
        slug = f"{base}-{counter}"
    return slug


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


def validate_project_form(form) -> tuple[dict, list[str]]:
    title = (form.get("title") or "").strip()
    authors = (form.get("authors") or "").strip()
    field = (form.get("field") or "").strip()
    abstract = (form.get("abstract") or "").strip()
    doi = (form.get("doi") or "").strip()
    institution = (form.get("institution") or "").strip()
    pmid = (form.get("pmid") or "").strip()
    visibility = (form.get("visibility") or "private").strip().lower()
    project_type = (form.get("project_type") or "research_project").strip().lower()
    workflow_stage = (form.get("workflow_stage") or "in_progress").strip().lower()
    year_raw = (form.get("year") or "").strip()
    errors = []

    if visibility not in PROJECT_VISIBILITIES:
        errors.append("Invalid visibility option.")
    if project_type not in PROJECT_TYPES:
        errors.append("Invalid project type.")
    if workflow_stage not in PROJECT_WORKFLOW_STAGES:
        errors.append("Invalid project stage.")

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

    if field and field not in ACADEMIC_FIELDS:
        errors.append("Invalid field selection.")

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
            "project_type": project_type,
            "workflow_stage": workflow_stage,
            "visibility": visibility,
            "is_public": visibility == "public",
        },
        errors,
    )


# Internal compatibility for routes and admin actions that have not yet been
# renamed. All new user-facing flows call validate_project_form().
validate_paper_form = validate_project_form


def paper_is_deleted(paper: Paper | None) -> bool:
    return not paper or (paper.status or "active").lower() == "deleted"


def project_visibility(project: Paper | None) -> str:
    """Return v26 visibility while honouring pre-v26 ``is_public`` rows."""
    if not project:
        return "private"
    return getattr(project, "visibility", None) or (
        "public" if getattr(project, "is_public", False) else "private"
    )


def new_project_share_token() -> str:
    """Opaque, stable identifier for an unlisted reviewer link."""
    return secrets.token_urlsafe(24)


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


def _apply_model_appearance_change(model, form):
    """Parse and apply an appearance/description change to a model's GLB.

    Shared by the owner route (model_appearance_update) and the admin
    override (admin_model_appearance_update) so the GLB backup/restore-on-
    failure logic and R2 re-mirror never diverge between the two callers.

    Returns (ok, message, flash_category, changes). ``changes`` is the dict
    of fields actually applied, for the caller's own audit log entry (the two
    callers use different event names) — or None on failure.
    """
    color_command = form.get("color_command")
    color_input = (form.get("color") or "").strip() or None
    # text command takes precedence so users can type "make it light gray".
    parsed = color_from_command(color_command) if color_command else None
    new_color = parsed or color_input
    if not new_color or HEX_COLOR_PATTERN.fullmatch(new_color) is None:
        return False, "Provide a valid hex color (#RRGGBB) or a known color name.", "danger", None

    rgba = hex_to_rgba(new_color)
    if rgba is None:
        return False, "Invalid color value.", "danger", None

    roughness_raw = form.get("roughness")
    metallic_raw = form.get("metallic")
    try:
        roughness = max(0.0, min(1.0, float(roughness_raw))) if roughness_raw else (model.appearance_roughness or 0.35)
        metallic = max(0.0, min(1.0, float(metallic_raw))) if metallic_raw else (model.appearance_metallic or 0.05)
    except (ValueError, TypeError):
        return False, "Provide valid roughness and metallic values (0–1).", "danger", None

    ar_placement_raw = form.get("ar_placement")
    ar_placement = ar_placement_raw if ar_placement_raw in ("floor", "wall") else (model.ar_placement or "floor")

    glb_path = model.glb_path
    # Restore the working GLB from the R2 mirror if the local copy is gone.
    ensure_local(glb_path, f"converted/{model.id}/model.glb")
    backup_path = glb_path + APPEARANCE_BACKUP_SUFFIX
    try:
        if os.path.exists(glb_path):
            shutil.copy2(glb_path, backup_path)
        try:
            # Textured models (e.g. FBX exports with baseColor/normal/metallic-
            # roughness maps) keep their artwork: only the metallic/roughness
            # factors are tuned, so dropping metallic to 0 removes a golden FBX
            # sheen without flattening the texture to a solid colour. Untextured
            # models still get the solid-colour enrichment (the color picker).
            if has_base_color_textures(glb_path):
                apply_pbr_factors(glb_path, roughness=roughness, metallic=metallic)
            else:
                enrich_glb_for_ar(glb_path, rgba, roughness=roughness, metallic=metallic)
        except Exception as exc:
            logger.exception("Appearance enrichment failed; restoring backup")
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, glb_path)
            glb_exists = os.path.exists(glb_path)
            detail = f" (GLB {'found' if glb_exists else 'NOT found'} at {glb_path}; {type(exc).__name__}: {exc})"
            return False, f"The model appearance could not be updated. The previous version is still active.{detail}", "warning", None

        model.appearance_color = new_color
        model.appearance_roughness = roughness
        model.appearance_metallic = metallic
        model.ar_placement = ar_placement
        # The edit page saves the model name/note in this same form (one
        # "Save Changes"). Only touch them when the fields are present so the
        # inline registry color form (which omits them) can't wipe them.
        if "display_name" in form:
            model.display_name = (form.get("display_name") or "").strip()[:255] or None
        if "description" in form:
            model.description = (form.get("description") or "").strip()[:5000] or None
        try:
            model.file_size = os.path.getsize(glb_path)
        except OSError:
            pass
        db.session.commit()
        # Re-mirror the rewritten GLB so R2 doesn't keep the pre-recolor copy.
        mirror_file(glb_path, f"converted/{model.id}/model.glb")
        changes = {"color": new_color, "roughness": roughness, "metallic": metallic, "ar_placement": ar_placement}
        return True, "Changes saved.", "success", changes
    except SQLAlchemyError:
        db.session.rollback()
        if os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, glb_path)
            except OSError:
                logger.exception("Failed to restore appearance backup after DB error")
        return False, "The model appearance could not be updated.", "danger", None
    finally:
        if os.path.exists(backup_path):
            cleanup_file(backup_path)


ADMIN_CSV_EXPORT_ROW_LIMIT = 5000


def _csv_response(rows: list[dict], columns: list[str], filename: str, truncated: bool = False) -> Response:
    """Build a downloadable CSV response from a list of plain dicts.

    ``truncated`` only affects the note appended in the admin route's flash
    message (via a query-string echo), not the file itself — the file always
    contains exactly the rows it was given.
    """
    import csv
    import io

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    response = Response(buf.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response


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

    if project_visibility(paper) == "unlisted":
        if not paper.share_token:
            paper.share_token = secrets.token_urlsafe(24)
            db.session.commit()
        target_url = public_url("project_share", share_token=paper.share_token)
    else:
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
        track_event(
            "model_conversion_failed",
            owner_user_id=model.user_id,
            project_id=model.paper_id,
            model_id=model.id,
            properties={"replacement": is_replacement},
        )
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
    # Repair FBX2glTF's opacity-to-alpha mapping bug: materials that carried an
    # FBX transparency channel can land with baseColorFactor alpha 0.0 (fully
    # transparent → textured but invisible, e.g. tree foliage). Restore them so
    # the texture renders. Runs before optimize so prune/webp see final alpha.
    repair_transparent_base_color(glb_path)
    # Foliage/leaf-card materials come out of FBX2glTF as alphaMode=BLEND, which
    # renders the alpha-cutout cards washed/semi-transparent (white leaves) — worse
    # in iOS AR. Switch textured BLEND materials to MASK (alpha cutout) for crisp
    # foliage in both model-viewer and AR.
    mask_cutout_textures(glb_path)
    # Bake a non-white baseColorFactor tint into its baseColorTexture so the colour
    # lives in the texture itself. Spec-gloss FBX (after metalrough conversion) keeps
    # the diffuse colour in baseColorFactor with a neutral texture; model-viewer
    # renders texture × factor fine, but Blender's USD exporter drops the factor
    # multiply, so iOS AR shows grey foliage. Runs before optimize_glb (which prunes
    # the orphaned originals) and before validation. Web output is unchanged (the
    # product is computed in linear space, exactly as the renderer would).
    bake_base_color_factor(glb_path)
    ensure_pbr_materials(glb_path)
    # Tame absurd sizes (almost always a unit error, e.g. an FBX mis-converted to
    # ~150 m) so AR doesn't place a giant model you stand inside. Runs before
    # optimize_glb because Draco-compressed geometry can't be measured. Legitimate
    # large models (under AR_MAX_PLAUSIBLE_EXTENT_M) are left untouched.
    clamp_oversized_glb(glb_path)
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
        if job is not None and job.status != "processing":
            # The worker path (run_next_conversion_job) already claimed the job
            # (status/started_at/attempts) under the row lock. Only the inline
            # path (tests / DEV_INLINE_JOBS) reaches here still 'pending', so
            # claim once here — incrementing again would double-count attempts
            # and halve the production retry budget.
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

            # On a replacement the GLB was just swapped for new geometry, so a
            # USDZ left from the previous version is stale — drop it so the iOS
            # AR companion is regenerated from the new GLB (otherwise Quick Look
            # keeps serving the old model).
            if is_replacement and os.path.exists(usdz_path):
                cleanup_file(usdz_path)
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
            reapply_model_license(model)

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
            track_event(
                "model_conversion_completed",
                owner_user_id=model.user_id,
                project_id=model.paper_id,
                model_id=model.id,
                properties={"source_format": source_format, "replacement": is_replacement},
            )
            # Mirror synchronously in the worker: the previous fire-and-forget
            # daemon threads could be killed when the worker exits/redeploys,
            # leaving a model marked "ready" whose files never reached R2 (then
            # lost on the next ephemeral-volume recycle).
            mirror_ok = mirror_directory_sync(
                os.path.join(app.config["CONVERTED_FOLDER"], model_id),
                f"converted/{model_id}",
            )
            model.r2_mirror_failed_at = None if mirror_ok else datetime.now(UTC)
            db.session.commit()
            if not mirror_ok:
                logger.error(
                    "R2 mirror incomplete for model %s; converted files may be "
                    "lost if the local volume is recycled.",
                    model_id,
                )
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


def reclaim_stuck_conversion_jobs(app: Flask) -> int:
    """Recover ConversionJob rows wedged in "processing".

    A job is only ever set to "processing" by an active worker; if it stays
    there past ``STUCK_JOB_TIMEOUT_SECONDS`` the worker that claimed it died
    mid-conversion (e.g. OOM-killed on a huge mesh) without ever reaching
    ``mark_model_failed``. Such jobs are reset to "pending" for another attempt,
    or marked "failed" (and the model surfaced as failed) once they have already
    burned through ``max_attempts``. Returns the number of jobs reclaimed.
    """
    timeout = int(app.config.get("STUCK_JOB_TIMEOUT_SECONDS", 3600) or 0)
    if timeout <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(seconds=timeout)
    # Only fetch rows that could actually be stale: aged past the cutoff, or
    # missing a start timestamp (so we can stamp one). Fresh, legitimately
    # running jobs are left untouched without loading them.
    candidates = (
        ConversionJob.query
        .filter(ConversionJob.status == "processing")
        .filter(or_(ConversionJob.started_at < cutoff, ConversionJob.started_at.is_(None)))
        .all()
    )
    reclaimed = 0
    for job in candidates:
        started = job.started_at
        if started is None:
            # No start timestamp to age out against; stamp one so the next pass
            # can reclaim it rather than leaving it wedged indefinitely. This
            # counts as a reclaim so the commit below actually runs — otherwise
            # the stamp is discarded and the job stays NULL/processing forever.
            job.started_at = datetime.now(UTC)
            reclaimed += 1
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if started >= cutoff:
            continue
        # model_replace jobs carry is_replacement so the model is surfaced as
        # "replacement_failed" (the previous working GLB keeps being served)
        # instead of a hard "failed" that would break the live viewer/QR.
        is_replacement = bool((job.payload or {}).get("is_replacement"))
        if (job.attempts or 0) >= (job.max_attempts or 3):
            app.logger.warning(
                "Reaping stuck conversion job %s (model=%s, attempts=%s): retry limit reached, marking failed.",
                job.id, job.model_id, job.attempts,
            )
            mark_model_failed(
                job.model_id,
                "Conversion did not finish (worker stopped) and the retry limit was reached.",
                is_replacement=is_replacement,
                job=job,
            )
        else:
            app.logger.warning(
                "Reaping stuck conversion job %s (model=%s, attempts=%s): re-queuing for another attempt.",
                job.id, job.model_id, job.attempts,
            )
            job.status = "pending"
            job.started_at = None
        reclaimed += 1
    if reclaimed:
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return 0
    return reclaimed


def run_next_conversion_job(app: Flask) -> bool:
    """Pick up the oldest pending ConversionJob and run it.

    Used by ``worker.py`` for the Railway worker service. Returns True when a
    job was processed (or attempted), so the caller can immediately poll for
    the next job rather than sleeping.
    """
    with app.app_context():
        # First recover any jobs a previously-crashed worker left wedged in
        # "processing" so they re-enter the queue instead of getting stuck.
        reclaim_stuck_conversion_jobs(app)
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
        # Stop retrying a job that has already exhausted its attempts so a
        # deterministically-failing conversion cannot loop forever. Preserve the
        # replacement semantics so a failed model_replace keeps serving the old
        # GLB instead of breaking the live viewer.
        if (job.attempts or 0) >= (job.max_attempts or 3):
            mark_model_failed(
                job.model_id,
                "Conversion failed repeatedly and reached the retry limit.",
                is_replacement=bool((job.payload or {}).get("is_replacement")),
                job=job,
            )
            return True
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
    # Self-serve uploads always start on the free (3-day) tier. Paid plans are
    # granted only through the checkout flow (upgrade_model_license) or an admin
    # override (admin_model_license_update); never trust a license picked on the
    # upload form, or a model could get a paid window for free. The institutional
    # plan is likewise a server-side grant (membership + quota check below).
    license_normalized = "free"
    display_name = (display_name or "").strip()[:255] or None
    description = (description or "").strip()[:5000] or None
    color = (color or "").strip() or None
    if color and HEX_COLOR_PATTERN.fullmatch(color) is None:
        color = None
    raw_source_unit = (source_unit or "").strip().lower()

    unique_id = str(uuid.uuid4())
    original_name = secure_filename(file.filename)
    source_format = original_name.rsplit(".", 1)[1].lower()

    # Source unit. STL/OBJ are unitless, so the user must explicitly declare
    # mm/cm/m (no magnitude guessing). FBX/GLB already carry real units, so they
    # are kept as authored ("embedded"). Validated before any files are written.
    if source_format in {"stl", "obj"}:
        if raw_source_unit not in {"mm", "cm", "m"}:
            return False, "Please choose the source unit (mm, cm, or m) for STL/OBJ files."
        source_unit_norm = raw_source_unit
    else:
        source_unit_norm = "embedded"

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

    # Institutional grant: a member's upload is covered by their institution's
    # contract while it is active, current, and within quota. Decided here
    # (file_size is known) so the per-file limit check below already enforces
    # the institutional plan's own cap.
    funding_institution = None
    institutional_fallback_reason = None
    membership = get_active_membership(paper.user_id)
    if membership is not None:
        can_fund, reason = institution_can_fund_upload(membership.institution, file_size)
        if can_fund:
            license_normalized = "institutional"
            funding_institution = membership.institution
        else:
            institutional_fallback_reason = reason

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
        source_unit=source_unit_norm,
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
    if funding_institution is not None:
        apply_institutional_license(model, funding_institution)
    else:
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
    audit_details = {}
    if funding_institution is not None:
        audit_details["institution_id"] = funding_institution.id
    elif institutional_fallback_reason:
        audit_details["institutional_fallback_reason"] = institutional_fallback_reason
    log_audit(
        "model_upload_queued",
        user_id=paper.user_id,
        resource_id=unique_id,
        details=audit_details or None,
    )
    track_event(
        "model_uploaded",
        owner_user_id=paper.user_id,
        project_id=paper.id,
        model_id=model.id,
        properties={"source_format": source_format},
    )
    message = "Model upload accepted. Processing has started in the background."
    if funding_institution is not None:
        message += f" Covered by {funding_institution.name}'s institutional license"
        if funding_institution.contract_ends_at is not None:
            message += f" until {funding_institution.contract_ends_at.strftime('%Y-%m-%d')}"
        message += "."
    elif institutional_fallback_reason in {"quota_models", "quota_storage"}:
        message += " Your institution's quota is currently full, so this model uses the free 3-day plan."
    elif institutional_fallback_reason in {"contract_expired", "suspended"}:
        message += " Your institution's contract is not active, so this model uses the free 3-day plan."
    return True, message


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
        log_audit(
            "rate_limit_exceeded",
            user_id=(current_user.id if current_user.is_authenticated else None),
            resource_id=request.endpoint,
            details={"path": request.path},
        )
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
        return render_template("pricing.html")

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

    @app.route("/demo/dashboard")
    def demo_dashboard():
        """Public, no-login preview of a researcher's dashboard, populated with
        fabricated sample publications. Mirrors the /demo/mitochondria/ar
        pattern: SimpleNamespace stand-ins + the real dashboard.html rendered
        with demo_mode=True (action links become sign-up CTAs)."""
        log_audit("demo_dashboard_opened", details={"source": request.args.get("source") or "direct"})
        now = datetime.now(UTC)

        def _paper(title, authors, institution, field, year, doi, pmid, is_public, has_pdf, model_count, age_days):
            paper = types.SimpleNamespace(
                title=title, authors=authors, institution=institution, field=field,
                year=year, doi=doi, pmid=pmid, is_public=is_public,
                pdf_path=("demo.pdf" if has_pdf else None),
                slug="demo-publication",
                created_at=now - timedelta(days=age_days),
                models=[],
            )
            paper.models = [
                types.SimpleNamespace(
                    id=f"demo-model-{age_days}-{index}",
                    display_name=f"{title.split()[0]} model {index + 1}",
                    original_filename=None,
                    poster_path=None,
                    created_at=now - timedelta(days=age_days, minutes=index),
                    paper=paper,
                )
                for index in range(model_count)
            ]
            return paper

        demo_papers = [
            _paper("Cellular Organelle Morphology in Human Hepatocytes",
                   "Dr. Sarah Chen, Prof. James Miller", "ETH Zurich — Department of Biology",
                   "Biology", 2025, "10.1234/cellbio.2025.mito", "39281047", True, True, 2, 3),
            _paper("Cranial Suture Fusion Patterns in Archaeological Samples",
                   "Dr. Elena Rossi", "University of Bologna — Archaeology",
                   "Archaeology", 2025, "10.1234/archaeo.2025.0142", None, True, False, 1, 12),
            _paper("Finite-Element Stress Model of a Cantilever Bracket",
                   "Prof. Ahmet Kaya, M. Demir", "METU — Mechanical Engineering",
                   "Engineering", 2024, None, None, False, True, 1, 27),
            _paper("Occlusal Anatomy from Intraoral Optical Scans",
                   "Dr. Laura Weber", "Charité — Department of Dentistry",
                   "Dentistry", 2024, "10.1234/dent.2024.0077", None, True, False, 3, 41),
        ]
        demo_models = sorted(
            (model for paper in demo_papers for model in paper.models),
            key=lambda model: model.created_at,
            reverse=True,
        )
        return render_template(
            "dashboard.html",
            papers=demo_papers,
            latest_models=demo_models[:6],
            institution_membership=None,
            demo_mode=True,
            demo_cross_url=url_for("demo_institution"),
            demo_cross_label="institution dashboard",
        )

    @app.route("/demo/institution")
    def demo_institution():
        """Public, no-login preview of the institution admin panel. All four
        tabs (overview / members / invites / models) render from the *real*
        panel templates with a fabricated (unsaved) Institution and sample
        rows, so domain_list() / contract_is_current() behave exactly as in
        production. ?tab= selects the section; sample counts are derived from
        the same rows so every tab agrees with the overview metrics."""
        tab = (request.args.get("tab") or "overview").lower()
        if tab not in {"overview", "members", "invites", "models"}:
            tab = "overview"
        log_audit(
            "demo_institution_opened",
            details={"source": request.args.get("source") or "direct", "tab": tab},
        )
        now = datetime.now(UTC)
        MB = 1024 * 1024
        demo_inst = Institution(
            name="Bogazici University — Research Computing",
            slug=None,  # no live public showcase for the demo; hides /i/<slug> links
            email_domains="boun.edu.tr, std.boun.edu.tr",
            status="active",
            contract_starts_at=now - timedelta(days=95),
            contract_ends_at=now + timedelta(days=270),
            quota_model_count=20,
            quota_storage_bytes=8 * 1024 * MB,
            public_description="Interactive 3D & AR models from Bogazici University research groups.",
            logo_path=None,
        )

        # --- Sample people (shared across members / contributors / authors) ---
        def _person(uid, name, email, role, age_days):
            return types.SimpleNamespace(
                id=uid, user_id=uid, role=role,
                user=types.SimpleNamespace(id=uid, username=name, email=email),
                joined_at=now - timedelta(days=age_days),
            )

        people = [
            _person(101, "Elif Demir", "elif.demir@boun.edu.tr", "admin", 214),
            _person(102, "Prof. Kenan Aksoy", "kenan.aksoy@boun.edu.tr", "admin", 176),
            _person(103, "Dr. Marco Rossi", "marco.rossi@boun.edu.tr", "member", 132),
            _person(104, "Zeynep Kaya", "zeynep.kaya@std.boun.edu.tr", "member", 61),
            _person(105, "Ayşe Yıldız", "ayse.yildiz@boun.edu.tr", "member", 33),
            _person(106, "Can Öztürk", "can.ozturk@boun.edu.tr", "member", 22),
            _person(107, "Dr. Leyla Şahin", "leyla.sahin@boun.edu.tr", "member", 13),
            _person(108, "Mehmet Arı", "mehmet.ari@std.boun.edu.tr", "member", 6),
        ]
        by_name = {p.user.username: p for p in people}
        recent_members = sorted(people, key=lambda p: p.joined_at, reverse=True)[:5]

        # --- Sample funded models. `is_public=False` rows demonstrate that a
        #     member can keep an upload private: it still counts against quota
        #     and shows here for the admin, but never links out or reaches the
        #     public showcase. Private examples are patient/cadaver material,
        #     the realistic reason a researcher withholds a model. ---
        def _model(mid, title, paper_title, slug, author, age_days, is_public, size_mb):
            person = by_name[author]
            return types.SimpleNamespace(
                id=mid, display_name=title,
                original_filename=title.lower().replace(" ", "_") + ".glb",
                paper=types.SimpleNamespace(
                    title=paper_title, slug=slug, is_public=is_public,
                    user_id=person.user_id,
                ),
                user=types.SimpleNamespace(id=person.user_id, username=author),
                created_at=now - timedelta(days=age_days),
                poster_path=None, file_size=size_mb * MB,
                license_type="institutional",
                access_expires_at=demo_inst.contract_ends_at,
            )

        models = [
            _model("demo-m1", "Hippocampal Neuron Reconstruction", "Dendritic Spine Density in Aging", "dendritic-spine-density", "Elif Demir", 1, True, 340),
            _model("demo-m2", "Zeolite Framework Unit Cell", "Pore Geometry of Microporous Solids", "pore-geometry-microporous", "Prof. Kenan Aksoy", 3, True, 180),
            _model("demo-m3", "Patient-Specific Aortic Aneurysm", "Endovascular Repair Planning (Case Series)", None, "Dr. Marco Rossi", 5, False, 870),
            _model("demo-m4", "Trabecular Bone Microarchitecture", "Load-Bearing Trabeculae under Stress", "load-bearing-trabeculae", "Dr. Marco Rossi", 8, True, 520),
            _model("demo-m5", "Fault Block Structural Model", "Extensional Tectonics in the Aegean", "extensional-tectonics-aegean", "Zeynep Kaya", 12, True, 460),
            _model("demo-m6", "Pre-op Mandible Reconstruction", "Reconstructive Surgery Workflow", None, "Dr. Leyla Şahin", 16, False, 690),
            _model("demo-m7", "Ottoman Ceramic Sherd Assemblage", "Glaze Composition of Iznik Ware", "glaze-composition-iznik", "Ayşe Yıldız", 20, True, 305),
            _model("demo-m8", "SARS-CoV-2 Spike Binding Pocket", "Inhibitor Docking at the RBD Interface", "inhibitor-docking-rbd", "Can Öztürk", 24, True, 270),
            _model("demo-m9", "Cadaveric Knee Ligament Study", "Anatomy Teaching Collection", None, "Mehmet Arı", 28, False, 740),
            _model("demo-m10", "Synaptic Vesicle Cryo-EM Density", "Presynaptic Architecture at Nanoscale", "presynaptic-architecture", "Elif Demir", 41, True, 610),
            _model("demo-m11", "Coronary Artery Calcium Model", "CT-Derived Plaque Morphology", None, "Dr. Marco Rossi", 55, False, 820),
            _model("demo-m12", "Microfluidic Chip Lattice", "Lab-on-Chip Flow Simulation", "lab-on-chip-flow", "Elif Demir", 72, True, 150),
        ]
        recent_models = models[:4]

        # Counts are derived from the sample rows so tabs never contradict.
        contrib_counts = {}
        for m in models:
            contrib_counts[m.user.username] = contrib_counts.get(m.user.username, 0) + 1
        top_contributors = [
            {"user": types.SimpleNamespace(username=name), "model_count": count}
            for name, count in sorted(contrib_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ]

        # --- Sample invite links ---
        def _invite(iid, token, expires_days, use_count, max_uses, state):
            return {
                "invite": types.SimpleNamespace(
                    id=iid, token=token,
                    expires_at=(now + timedelta(days=expires_days)) if expires_days else None,
                    use_count=use_count, max_uses=max_uses,
                    revoked_at=(now - timedelta(days=2)) if state == "revoked" else None,
                ),
                "state": state,
                "join_url": public_url("institution.join", token=token),
            }

        invites = [
            _invite(1, "demo-lab-neuro-2026", 12, 3, 25, "active"),
            _invite(2, "demo-open-seminar", None, 8, None, "active"),
            _invite(3, "demo-old-cohort", -4, 5, 5, "revoked"),
        ]

        def _page(items):
            # pages=1 keeps the pagination control hidden (its login-gated
            # page links would 401 in the demo); the sample fits one page.
            return types.SimpleNamespace(items=items, total=len(items), pages=1, page=1)

        usage_bytes = sum(m.file_size for m in models)
        common = dict(
            institution=demo_inst,
            logo_url=url_for("static", filename="images/demo-institution-logo.svg"),
            active_tab=tab,
            demo_mode=True,
            demo_cross_url=url_for("demo_dashboard"),
            demo_cross_label="researcher dashboard",
        )

        if tab == "members":
            return render_template("institution/members.html", members_pagination=_page(people), **common)
        if tab == "invites":
            return render_template("institution/invites.html", invites=invites, **common)
        if tab == "models":
            return render_template("institution/models.html", models_pagination=_page(models), **common)

        return render_template(
            "institution/overview.html",
            usage_models=len(models),
            usage_bytes=usage_bytes,
            member_count=len(people),
            active_invites=sum(1 for row in invites if row["state"] == "active"),
            recent_members=recent_members,
            recent_models=recent_models,
            models_last_30d=sum(1 for m in models if (now - m.created_at).days < 30),
            top_contributors=top_contributors,
            **common,
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

    @app.route("/refund-policy")
    def refund_policy():
        return render_template("legal/refund_policy.html")

    @app.route("/distance-sales-agreement")
    def distance_sales_agreement():
        return render_template("legal/distance_sales_agreement.html")

    @app.route("/contact-info")
    def contact_info():
        return render_template("legal/contact_info.html")

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
        # Hidden for now (no inbox). Re-enable with CONTACT_ENABLED=1.
        if not current_app.config.get("CONTACT_ENABLED"):
            abort(404)
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

    @app.route("/institutional", methods=["GET", "POST"])
    @limiter.limit("5 per hour", methods=["POST"])
    def institutional_inquiry():
        # Unlike /contact this is NOT gated by CONTACT_ENABLED: B2B leads are
        # the point of the institutional offering, so the form stays live even
        # while the general contact inbox is disabled.
        if request.method == "POST":
            institution_name = (request.form.get("institution_name") or "").strip()[:200]
            contact_name = (request.form.get("contact_name") or "").strip()[:120]
            email = (request.form.get("email") or "").strip()[:200]
            estimated_members = (request.form.get("estimated_members") or "").strip()[:40]
            message = (request.form.get("message") or "").strip()[:5000]
            if not institution_name or not contact_name or not email:
                flash("Please fill in the institution name, your name, and your work email.", "danger")
                return redirect(url_for("institutional_inquiry"))
            if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
                flash("Please enter a valid work email address.", "danger")
                return redirect(url_for("institutional_inquiry"))
            from utils.email import send_email

            recipient = current_app.config.get("CONTACT_EMAIL") or "info@academicar.com"
            send_email(
                recipient,
                "AcademicAR institutional inquiry",
                (
                    f"Institution: {institution_name}\n"
                    f"Contact: {contact_name} <{email}>\n"
                    f"Estimated members: {estimated_members or '-'}\n\n"
                    f"{message or '(no message)'}"
                ),
            )
            log_audit(
                "institution_inquiry_submitted",
                details={"institution_name": institution_name, "from_email": email},
            )
            flash("Thanks — we received your inquiry and will reach out shortly.", "success")
            return redirect(url_for("institutional_inquiry"))
        return render_template("institutional.html")

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

    @app.route("/i/<slug>")
    def institution_showcase(slug):
        """Public showcase for an institution: its public publications that
        contain institution-funded models. Marketing surface for the B2B
        side; no login required."""
        institution = Institution.query.filter_by(slug=slug).first()
        if institution is None:
            abort(404)
        papers = (
            Paper.query.options(selectinload(Paper.models))
            .join(Model3D, Model3D.paper_id == Paper.id)
            .filter(
                Model3D.institution_id == institution.id,
                Model3D.license_type == "institutional",
                Paper.is_public.is_(True),
                or_(Paper.status.is_(None), Paper.status != "deleted"),
            )
            .distinct()
            .order_by(Paper.created_at.desc())
            .limit(200)
            .all()
        )
        model_count, _bytes_used = institution_usage(institution.id)
        member_count = InstitutionMember.query.filter_by(institution_id=institution.id).count()
        return render_template(
            "institution/showcase.html",
            institution=institution,
            papers=papers,
            model_count=model_count,
            member_count=member_count,
        )

    @app.route("/institution-logos/<path:filename>")
    def serve_institution_logo(filename):
        safe = os.path.basename(filename)
        local = os.path.join(app.config["INSTITUTION_LOGO_FOLDER"], safe)
        if not os.path.exists(local):
            ensure_local(local, f"institution_logos/{safe}")
        if not os.path.exists(local):
            abort(404)
        response = send_from_directory(app.config["INSTITUTION_LOGO_FOLDER"], safe, conditional=True)
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

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
        if current_app.config.get("APP_ENV") not in {"production", "prod", "pilot"}:
            return current_app.response_class("User-agent: *\nDisallow: /\n", mimetype="text/plain")
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
            "blog_index",
            "terms",
            "privacy",
            "data_protection",
            "refund_policy",
            "distance_sales_agreement",
            "contact_info",
            "demo_mitochondria_ar",
            "demo_dashboard",
            "demo_institution",
        ):
            try:
                urls.append(public_url(endpoint))
            except Exception:
                continue
        if current_app.config.get("CONTACT_ENABLED"):
            try:
                urls.append(public_url("contact"))
            except Exception:
                pass
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
        # Institution showcases with at least one public funded paper.
        showcase_institutions = (
            Institution.query.filter(Institution.slug.isnot(None))
            .join(Model3D, Model3D.institution_id == Institution.id)
            .join(Paper, Model3D.paper_id == Paper.id)
            .filter(
                Model3D.license_type == "institutional",
                Paper.is_public.is_(True),
                db.or_(Paper.status.is_(None), Paper.status != "deleted"),
            )
            .distinct()
            .all()
        )
        for institution in showcase_institutions:
            try:
                urls.append(public_url("institution_showcase", slug=institution.slug))
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
        if plan_key not in PAID_PLAN_KEYS or not get_license_plan(plan_key).is_purchasable:
            flash("Choose a valid paid plan to upgrade this model.", "danger")
            return redirect(request.referrer or url_for("dashboard"))

        # Never let this paid route apply a downgrade (a plan cheaper than the
        # model already has) — that would charge the user to *reduce* access.
        # Renewing the same plan (equal price) and upgrading (higher price) are
        # both fine. Downgrades are an admin-only action via /models/<id>/license.
        if get_license_plan(plan_key).price_usd < get_license_plan(model.license_type).price_usd:
            flash(
                "This model already has a higher-tier license. You can renew it "
                "or upgrade to a longer plan, but not switch to a cheaper one here.",
                "info",
            )
            return redirect(request.referrer or url_for("dashboard"))

        provider = get_payment_provider()
        payment_currency = current_app.config.get("PAYMENT_CURRENCY", "USD")
        try:
            amount_kurus = plan_amount_minor_units(plan_key, payment_currency)
        except ForexRateUnavailable:
            flash("Could not start the upgrade: exchange rate unavailable. Please try again shortly.", "danger")
            return redirect(request.referrer or url_for("dashboard"))
        payment = Payment(
            user_id=current_user.id,
            paper_id=model.paper_id,
            model_id=model.id,
            plan_key=plan_key,
            amount_kurus=amount_kurus,
            currency=payment_currency,
            provider=provider.name,
            provider_reference=f"{provider.name}-{uuid.uuid4().hex[:12]}",
            status="pending",
        )
        db.session.add(payment)
        try:
            db.session.flush()
            payment.invoice_number = build_invoice_number(payment.id)
            # Land the buyer back on the publication page (their management hub)
            # with a success marker, NOT on the viewer: the license is granted by
            # the async provider webhook, so a viewer landing can race ahead of it
            # and render the 410 "access unavailable" state to someone who just
            # paid. paper_detail always renders for the owner and shows the banner.
            success_url = public_url("paper_detail", slug=model.paper.slug, upgraded=model.id)
            # A cancelled/failed payment returns to the plan picker so they can retry.
            cancel_url = public_url("model_upgrade_page", model_id=model.id)
            checkout_url = provider.create_checkout(
                payment=payment,
                model=model,
                plan_key=plan_key,
                user=current_user,
                success_url=success_url,
                cancel_url=cancel_url,
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

    @app.route("/models/<model_id>/upgrade", methods=["GET"])
    @login_required
    @require_model_ownership
    def model_upgrade_page(model_id):
        """Per-model licensing page: pick a plan and continue to the payment
        provider's checkout (the form posts to upgrade_model_license)."""
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        return render_template(
            "model_upgrade.html",
            model=model,
            paper=model.paper,
            options=model_upgrade_options(model),
            current_plan=get_license_plan(model.license_type),
            access_state=model_access_status(model),
        )

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
            # A signature mismatch could mean a forged callback or a
            # misconfigured merchant key/salt; leave a durable trail like every
            # other webhook rejection branch below, instead of a bare 400.
            log_audit(
                "payment_webhook_signature_invalid",
                resource_id=provider_name,
                details={"path": request.path},
            )
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

        # Lock the Payment row on engines that support it so two concurrent,
        # identical webhooks cannot both clear the paid-check below and
        # double-process the same order. Applied to BOTH lookup paths
        # (payment_id and provider_reference).
        lockable = db.engine.dialect.name in {"postgresql", "mysql"}
        payment = None
        payment_id = event.get("payment_id")
        if payment_id is not None:
            try:
                payment = db.session.get(
                    Payment, int(payment_id), with_for_update=lockable or None
                )
            except (TypeError, ValueError):
                payment = None
        if payment is None and provider_reference:
            ref_query = Payment.query.filter_by(provider_reference=provider_reference)
            if lockable:
                ref_query = ref_query.with_for_update()
            payment = ref_query.first()
        if payment is not None and payment.status == "paid":
            return ack(duplicate=True)

        # Recover the plan from the stored Payment when the callback omits it
        # (PayTR's notification carries no custom data) BEFORE we decide whether
        # to act, so the recovery can rescue an otherwise unmappable event.
        if effective_plan is None and payment is not None and (payment.plan_key or "") in PAID_PLAN_KEYS:
            effective_plan = payment.plan_key

        # A paid notification we cannot map to a buyable paid plan must NOT fall
        # through to apply_successful_payment: that normalises None -> "free" and
        # would silently downgrade the model's license (and create a bogus zero
        # amount Payment row). Acknowledge so the gateway stops retrying, but
        # leave any existing license untouched.
        if effective_plan is None:
            current_app.logger.warning(
                "Paid webhook with no resolvable paid plan; ignoring (provider=%s, ref=%s)",
                provider.name,
                provider_reference,
            )
            # Leave a durable audit record so a real payment that cannot be
            # mapped to a plan can be reconciled manually (app logs are not a
            # reliable financial trail).
            log_audit(
                "payment_webhook_unmapped",
                user_id=(payment.user_id if payment else None),
                resource_id=(payment.model_id if payment else None),
                details={
                    "provider": provider.name,
                    "provider_reference": provider_reference,
                    "payment_id": (payment.id if payment else None),
                    "event_plan_key": plan_key or None,
                },
            )
            return ack(ignored=True, reason="no_plan")

        # Which model gets the license is decided by the stored Payment, never by
        # an unverified event field. If the event also names a model_id it must
        # match the Payment's, otherwise a (replayed/crafted) callback could grant
        # the paid license to a model the payer never bought (IDOR).
        if payment is not None and payment.model_id:
            if model_id and str(model_id) != str(payment.model_id):
                log_audit(
                    "payment_model_mismatch",
                    user_id=payment.user_id,
                    resource_id=payment.model_id,
                    details={
                        "provider": provider.name,
                        "payment_id": payment.id,
                        "event_model_id": str(model_id),
                        "provider_reference": provider_reference,
                    },
                )
                db.session.rollback()
                return jsonify({"ok": False, "error": "model_mismatch"}), 400
            model = payment.model
        else:
            model = db.session.get(Model3D, model_id) if model_id else (payment.model if payment else None)

        if payment is None:
            payment_currency = current_app.config.get("PAYMENT_CURRENCY", "USD")
            try:
                amount_kurus = plan_amount_minor_units(effective_plan, payment_currency)
            except ForexRateUnavailable:
                current_app.logger.error(
                    "FX rate unavailable while reconstructing an orphaned payment "
                    "(provider=%s, ref=%s); returning 500 so the gateway retries.",
                    provider.name, provider_reference,
                )
                return jsonify({"ok": False, "error": "fx_rate_unavailable"}), 500
            payment = Payment(
                user_id=(model.user_id if model else None),
                paper_id=(model.paper_id if model else None),
                model_id=(model.id if model else None),
                plan_key=effective_plan,
                amount_kurus=amount_kurus,
                currency=payment_currency,
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

        # Defense-in-depth: the signature only proves the callback is authentic,
        # not that the correct amount was captured. Reject a "paid" event that
        # reports less than what we asked the gateway to charge (both in the same
        # minor units, set together at checkout) before granting the license.
        paid_minor = event.get("amount_minor")
        if (
            paid_minor is not None
            and payment is not None
            and payment.amount_kurus
            and int(paid_minor) < int(payment.amount_kurus)
        ):
            log_audit(
                "payment_amount_mismatch",
                user_id=(payment.user_id or (model.user_id if model else None)),
                resource_id=(model.id if model else None),
                details={
                    "provider": provider.name,
                    "expected_minor": int(payment.amount_kurus),
                    "paid_minor": int(paid_minor),
                    "provider_reference": provider_reference,
                },
            )
            db.session.rollback()
            return jsonify({"ok": False, "error": "amount_mismatch"}), 400

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
        track_event("qr_scanned", owner_user_id=model.user_id, project_id=model.paper_id, model_id=model.id)
        status = model_access_status(model)
        is_owner = current_user.is_authenticated and current_user.id == model.user_id
        if status != "active":
            return (
                render_template(
                    "model_access_unavailable.html",
                    model=model,
                    paper=model.paper,
                    status=status,
                    is_owner=is_owner,
                ),
                410,
            )
        usdz_path = os.path.join(
            app.config["CONVERTED_FOLDER"], model.id, "model.usdz"
        )
        if not os.path.exists(usdz_path):
            ensure_local(usdz_path, f"converted/{model.id}/model.usdz")
        has_usdz = os.path.exists(usdz_path)
        log_audit(
            "public_model_viewed",
            user_id=current_user.id if current_user.is_authenticated else None,
            resource_id=model.id,
            details={"paper_id": model.paper_id, "public_id": model.public_id},
        )
        annotations = ModelAnnotation.query.filter_by(model_id=model.id).order_by(ModelAnnotation.order_index).all()
        track_event("model_viewed", owner_user_id=model.user_id, project_id=model.paper_id, model_id=model.id)
        scale_ref = human_scale_reference(format_model_dimensions_cm(model))
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
        # An admin-disabled QR link must stop resolving (takedown/abuse control);
        # otherwise the disable action is a no-op.
        if qr_link is not None and qr_link.status != "active":
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
            is_owner = current_user.is_authenticated and current_user.id == model.user_id
            return (
                render_template(
                    "model_access_unavailable.html",
                    model=model,
                    paper=model.paper,
                    status=status,
                    is_owner=is_owner,
                ),
                410,
            )
        return redirect(url_for("view_model", model_id=model.id))

    @app.route("/analytics/event", methods=["POST"])
    @csrf.exempt
    def analytics_browser_event():
        """Accept a very small allowlist of anonymous viewer interactions."""
        payload = request.get_json(silent=True) or {}
        event_name = (payload.get("event") or "").strip()
        model_id = (payload.get("model_id") or "").strip()
        if event_name not in ALLOWED_BROWSER_EVENTS or not is_uuid(model_id):
            return jsonify({"ok": False}), 400
        model = db.session.get(Model3D, model_id)
        if not model or not _paper_visible_to_request(model.paper):
            return jsonify({"ok": False}), 404
        track_event(event_name, owner_user_id=model.user_id, project_id=model.paper_id, model_id=model.id)
        return jsonify({"ok": True}), 202

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
        if not model:
            abort(404)
        if not _paper_visible_to_request(model.paper) or not model_is_accessible(model):
            abort(404)
        directory = os.path.join(app.config["CONVERTED_FOLDER"], unique_id)
        poster = os.path.join(directory, "poster.png")
        if not os.path.exists(poster):
            ensure_local(poster, f"converted/{unique_id}/poster.png")
        if not os.path.exists(poster):
            glb_path = os.path.join(directory, "model.glb")
            if not os.path.exists(glb_path):
                ensure_local(glb_path, f"converted/{unique_id}/model.glb")
            if not os.path.exists(glb_path) or not generate_poster(glb_path, poster):
                abort(404)
            model.poster_path = poster
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
                abort(500)
            mirror_file(poster, f"converted/{unique_id}/poster.png")
            log_audit("model_poster_backfilled", user_id=model.user_id, resource_id=model.id)
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
        """A project is visible if shared publicly/unlisted, or owned by user."""
        if paper_is_deleted(paper):
            return False
        if project_visibility(paper) in {"public", "unlisted"}:
            return True
        return current_user.is_authenticated and current_user.id == paper.user_id

    @app.route("/qr-image/paper/<int:paper_id>")
    def qr_image_paper(paper_id):
        paper = db.session.get(Paper, paper_id)
        if not paper:
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
        # Unlisted projects are intentionally not reachable through their
        # human-readable slug; reviewers receive the opaque /share/ URL.
        if project_visibility(paper) == "unlisted" and not (
            current_user.is_authenticated and current_user.id == paper.user_id
        ):
            abort(404)
        if not _paper_visible_to_request(paper):
            abort(404)
        track_event("project_viewed", owner_user_id=paper.user_id, project_id=paper.id)
        return render_template("paper_public.html", paper=paper)

    @app.route("/share/<share_token>")
    def project_share(share_token):
        paper = active_paper_query().filter_by(share_token=share_token).first_or_404()
        if project_visibility(paper) != "unlisted" and not _paper_visible_to_request(paper):
            abort(404)
        track_event("review_link_opened", owner_user_id=paper.user_id, project_id=paper.id)
        response = make_response(render_template("paper_public.html", paper=paper, unlisted_share=True))
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return apply_analytics_cookie(response)

    @app.route("/p/<slug>/pdf")
    def paper_public_pdf(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()
        if not _paper_visible_to_request(paper):
            abort(404)
        if not paper.pdf_path:
            abort(404)
        return render_template("pdf_reader.html", paper=paper)

    @app.route("/p/<slug>/pdf/file")
    def paper_public_pdf_file(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()
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
        latest_models = (
            Model3D.query
            .join(Paper)
            .options(selectinload(Model3D.paper))
            .filter(
                Model3D.user_id == current_user.id,
                Paper.deleted_at.is_(None),
            )
            .order_by(Model3D.created_at.desc())
            .limit(6)
            .all()
        )
        return render_template(
            "dashboard.html",
            papers=papers,
            latest_models=latest_models,
            analytics=analytics_snapshot(current_user.id),
            institution_membership=get_active_membership(current_user.id),
        )

    @app.route("/insights")
    @login_required
    def insights():
        return render_template("insights.html", analytics=analytics_snapshot(current_user.id))

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
            "jobs",
            "annotations",
            "access",
            "revenue",
            "pricing",
            "security",
            "storage",
            "system",
            "logs",
            "backups",
            "blog",
            "institutions",
            "analytics",
        }
        if admin_page not in admin_pages:
            abort(404)
        page = max(request.args.get("page", type=int) or 1, 1)
        user_query_text = (request.args.get("user_q") or "").strip()
        user_role_filter = (request.args.get("user_role") or "all").strip().lower()
        model_status_filter = (request.args.get("model_status") or "all").strip().lower()
        job_status_filter = (request.args.get("job_status") or "all").strip().lower()
        audit_event_filter = (request.args.get("audit_event") or "all").strip().lower()
        audit_query_text = (request.args.get("audit_q") or "").strip()
        audit_user_filter = (request.args.get("audit_user") or "").strip()
        paper_query_text = (request.args.get("paper_q") or "").strip()
        paper_visibility_filter = (request.args.get("paper_visibility") or "all").strip().lower()
        paper_status_filter = (request.args.get("paper_status") or "all").strip().lower()
        pay_status_filter = (request.args.get("pay_status") or "all").strip().lower()
        pay_provider_filter = (request.args.get("provider") or "all").strip().lower()
        pay_query_text = (request.args.get("pay_q") or "").strip()
        qr_status_filter = (request.args.get("qr_status") or "all").strip().lower()
        qr_query_text = (request.args.get("qr_q") or "").strip()

        users_query = User.query
        if user_query_text:
            pattern = f"%{user_query_text.lower()}%"
            users_query = users_query.filter(
                or_(
                    func.lower(User.email).like(pattern),
                    func.lower(User.username).like(pattern),
                )
            )
        if user_role_filter == "admin":
            users_query = users_query.filter(User.is_admin.is_(True))
        elif user_role_filter == "member":
            users_query = users_query.filter(User.is_admin.is_(False))

        # Projects list (admin sees deleted rows too, so it can restore them).
        papers_query = Paper.query.options(selectinload(Paper.author))
        if paper_query_text:
            paper_pattern = f"%{paper_query_text.lower()}%"
            papers_query = papers_query.outerjoin(User, Paper.user_id == User.id).filter(
                or_(
                    func.lower(Paper.title).like(paper_pattern),
                    func.lower(Paper.slug).like(paper_pattern),
                    func.lower(User.email).like(paper_pattern),
                )
            )
        if paper_visibility_filter in PROJECT_VISIBILITIES:
            papers_query = papers_query.filter(Paper.visibility == paper_visibility_filter)
        if paper_status_filter == "active":
            papers_query = papers_query.filter(or_(Paper.status.is_(None), Paper.status != "deleted"))
        elif paper_status_filter == "deleted":
            papers_query = papers_query.filter(Paper.status == "deleted")

        models_query = Model3D.query
        if model_status_filter != "all":
            models_query = models_query.filter(Model3D.processing_status == model_status_filter)

        payments_query = Payment.query.options(selectinload(Payment.user), selectinload(Payment.institution))
        if pay_status_filter in {"pending", "paid", "failed", "refunded"}:
            payments_query = payments_query.filter(Payment.status == pay_status_filter)
        if pay_provider_filter in {"manual", "paytr"}:
            payments_query = payments_query.filter(Payment.provider == pay_provider_filter)
        if pay_query_text:
            pay_pattern = f"%{pay_query_text.lower()}%"
            payments_query = payments_query.outerjoin(User, Payment.user_id == User.id).filter(
                or_(
                    func.lower(Payment.invoice_number).like(pay_pattern),
                    func.lower(Payment.provider_reference).like(pay_pattern),
                    func.lower(User.email).like(pay_pattern),
                )
            )

        qr_query = QRLink.query.options(selectinload(QRLink.model))
        if qr_status_filter in {"active", "disabled"}:
            qr_query = qr_query.filter(QRLink.status == qr_status_filter)
        if qr_query_text:
            qr_pattern = f"%{qr_query_text.lower()}%"
            qr_query = qr_query.filter(
                or_(
                    func.lower(QRLink.public_id).like(qr_pattern),
                    func.lower(QRLink.model_id).like(qr_pattern),
                )
            )

        jobs_query = ConversionJob.query
        if job_status_filter != "all":
            jobs_query = jobs_query.filter(ConversionJob.status == job_status_filter)

        annotations_query = ModelAnnotation.query.options(
            selectinload(ModelAnnotation.model).selectinload(Model3D.paper)
        )

        audit_query = AuditLog.query
        if audit_event_filter != "all":
            audit_query = audit_query.filter(AuditLog.event_type == audit_event_filter)
        if audit_user_filter.isdigit():
            audit_query = audit_query.filter(AuditLog.user_id == int(audit_user_filter))
        if audit_query_text:
            audit_pattern = f"%{audit_query_text.lower()}%"
            audit_query = audit_query.filter(
                or_(
                    func.lower(AuditLog.event_type).like(audit_pattern),
                    func.lower(AuditLog.resource_id).like(audit_pattern),
                    func.lower(AuditLog.ip_address).like(audit_pattern),
                )
            )

        # Paginated lists (50 rows/page). Each page passes both the items (as the
        # existing template variable) and the Pagination object for the controls.
        users_pagination = users_query.order_by(User.created_at.desc()).paginate(
            page=page, per_page=ADMIN_PER_PAGE, error_out=False
        )
        users = users_pagination.items
        # Eager-load relationships the admin templates touch per row, to avoid an
        # N+1 query storm (each model row reads paper+author; each QR row reads model).
        papers_pagination = papers_query.order_by(Paper.created_at.desc()).paginate(
            page=page, per_page=ADMIN_PER_PAGE, error_out=False
        )
        papers = papers_pagination.items
        models_pagination = (
            models_query.options(selectinload(Model3D.paper).selectinload(Paper.author))
            .order_by(Model3D.created_at.desc())
            .paginate(page=page, per_page=ADMIN_PER_PAGE, error_out=False)
        )
        models = models_pagination.items
        payments_pagination = payments_query.order_by(Payment.created_at.desc()).paginate(
            page=page, per_page=ADMIN_PER_PAGE, error_out=False
        )
        payments = payments_pagination.items
        qr_pagination = qr_query.order_by(QRLink.created_at.desc()).paginate(
            page=page, per_page=ADMIN_PER_PAGE, error_out=False
        )
        qr_links = qr_pagination.items
        audit_pagination = audit_query.order_by(AuditLog.timestamp.desc()).paginate(
            page=page, per_page=ADMIN_PER_PAGE, error_out=False
        )
        audit_logs = audit_pagination.items
        jobs_pagination = jobs_query.order_by(ConversionJob.created_at.desc()).paginate(
            page=page, per_page=ADMIN_PER_PAGE, error_out=False
        )
        jobs = jobs_pagination.items
        annotations_pagination = annotations_query.order_by(ModelAnnotation.created_at.desc()).paginate(
            page=page, per_page=ADMIN_PER_PAGE, error_out=False
        )
        annotations = annotations_pagination.items
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
            # Exclude soft-deleted papers (and their models) from headline counts
            # so the dashboard reflects live content, consistent with
            # active_paper_query() used elsewhere.
            "papers": active_paper_query().count(),
            "public_papers": active_paper_query().filter_by(is_public=True).count(),
            "models": Model3D.query.filter(
                Model3D.paper.has(or_(Paper.status.is_(None), Paper.status != "deleted"))
            ).count(),
            "active_models": Model3D.query.filter(
                Model3D.paper.has(or_(Paper.status.is_(None), Paper.status != "deleted")),
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
        papers_with_doi = active_paper_query().filter(Paper.doi.isnot(None), Paper.doi != "").count()
        papers_with_pmid = active_paper_query().filter(Paper.pmid.isnot(None), Paper.pmid != "").count()
        private_papers = max(totals["papers"] - totals["public_papers"], 0)
        papers_with_pdf = active_paper_query().filter(Paper.pdf_path.isnot(None), Paper.pdf_path != "").count()
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
            "rate_limit_hits": AuditLog.query.filter_by(event_type="rate_limit_exceeded").count(),
            "webhook_signature_failures": AuditLog.query.filter_by(event_type="payment_webhook_signature_invalid").count(),
        }
        mirror_failed_count = Model3D.query.filter(Model3D.r2_mirror_failed_at.isnot(None)).count()
        mirror_failed_models = (
            Model3D.query.filter(Model3D.r2_mirror_failed_at.isnot(None))
            .order_by(Model3D.r2_mirror_failed_at.desc())
            .limit(20)
            .all()
            if admin_page == "storage"
            else []
        )
        critical_alerts = []
        if totals["failed_jobs"]:
            critical_alerts.append({
                "text": f"{totals['failed_jobs']} failed conversion job(s)",
                "url": url_for("admin_dashboard", admin_page="jobs", job_status="failed"),
            })
        if near_limit_models:
            critical_alerts.append({
                "text": f"{len(near_limit_models)} model(s) near storage limit",
                "url": url_for("admin_dashboard", admin_page="models"),
            })
        if sum(orphan_counts.values()):
            critical_alerts.append({
                "text": f"{sum(orphan_counts.values())} orphan file(s) detected",
                "url": url_for("admin_dashboard", admin_page="storage"),
            })
        if mirror_failed_count:
            critical_alerts.append({
                "text": f"{mirror_failed_count} model(s) failed to mirror to R2",
                "url": url_for("admin_dashboard", admin_page="storage"),
            })
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
        if admin_page == "blog":
            # Self-heal on every visit — same precedent as
            # `if admin_page == "backups": ensure_daily_backup(...)`.
            seed_builtin_blog_posts(app)
        blog_posts = (
            BlogPost.query.order_by(BlogPost.created_at.desc()).all() if admin_page == "blog" else []
        )
        editing_post = None
        if admin_page == "blog":
            edit_id = (request.args.get("edit") or "").strip()
            if edit_id.isdigit():
                editing_post = db.session.get(BlogPost, int(edit_id))
        institutions_rows = []
        institutions_pagination = None
        institution_member_counts = {}
        institution_usage_map = {}
        if admin_page == "institutions":
            institutions_pagination = Institution.query.order_by(Institution.created_at.desc()).paginate(
                page=page, per_page=ADMIN_PER_PAGE, error_out=False
            )
            institutions_rows = institutions_pagination.items
            institution_ids = [inst.id for inst in institutions_rows]
            if institution_ids:
                institution_member_counts = dict(
                    db.session.query(InstitutionMember.institution_id, func.count(InstitutionMember.id))
                    .filter(InstitutionMember.institution_id.in_(institution_ids))
                    .group_by(InstitutionMember.institution_id)
                    .all()
                )
                usage_rows = (
                    db.session.query(
                        Model3D.institution_id,
                        func.count(Model3D.id),
                        func.coalesce(func.sum(Model3D.file_size), 0),
                    )
                    .join(Paper, Model3D.paper_id == Paper.id)
                    .filter(
                        Model3D.institution_id.in_(institution_ids),
                        Model3D.license_type == "institutional",
                        or_(Paper.status.is_(None), Paper.status != "deleted"),
                    )
                    .group_by(Model3D.institution_id)
                    .all()
                )
                institution_usage_map = {
                    row[0]: {"models": int(row[1] or 0), "bytes": int(row[2] or 0)} for row in usage_rows
                }
        system_health = _admin_system_health() if admin_page == "system" else {}
        analytics = analytics_snapshot(days=30) if admin_page == "analytics" else None
        pricing_rows = []
        if admin_page == "pricing":
            # Self-heal on every visit — same precedent as
            # `if admin_page == "backups": ensure_daily_backup(...)`.
            seed_license_plans(app)
            plan_order = {"free": 0, "academic": 1, "extended_archive": 2, "institutional": 3}
            pricing_rows = sorted(LicensePlanConfig.query.all(), key=lambda r: plan_order.get(r.key, 99))
        return render_template(
            f"admin/{admin_page}.html",
            users=users,
            papers=papers,
            models=models,
            payments=payments,
            qr_links=qr_links,
            audit_logs=audit_logs,
            jobs=jobs,
            annotations=annotations,
            annotations_pagination=annotations_pagination,
            system_health=system_health,
            pricing_rows=pricing_rows,
            users_pagination=users_pagination,
            papers_pagination=papers_pagination,
            models_pagination=models_pagination,
            payments_pagination=payments_pagination,
            qr_pagination=qr_pagination,
            audit_pagination=audit_pagination,
            jobs_pagination=jobs_pagination,
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
            mirror_failed_count=mirror_failed_count,
            mirror_failed_models=mirror_failed_models,
            security_events=security_events,
            critical_alerts=critical_alerts,
            backups=backups,
            blog_posts=blog_posts,
            editing_post=editing_post,
            institutions=institutions_rows,
            institutions_pagination=institutions_pagination,
            institution_member_counts=institution_member_counts,
            institution_usage_map=institution_usage_map,
            active_page=admin_page,
            analytics=analytics,
            filters={
                "user_q": user_query_text,
                "user_role": user_role_filter,
                "model_status": model_status_filter,
                "job_status": job_status_filter,
                "audit_event": audit_event_filter,
                "audit_q": audit_query_text,
                "audit_user": audit_user_filter,
                "paper_q": paper_query_text,
                "paper_visibility": paper_visibility_filter,
                "paper_status": paper_status_filter,
                "pay_status": pay_status_filter,
                "provider": pay_provider_filter,
                "pay_q": pay_query_text,
                "qr_status": qr_status_filter,
                "qr_q": qr_query_text,
            },
        )

    @app.route("/admin/users/export.csv")
    @login_required
    def admin_users_export():
        require_admin()
        user_query_text = (request.args.get("user_q") or "").strip()
        user_role_filter = (request.args.get("user_role") or "all").strip().lower()
        query = User.query
        if user_query_text:
            pattern = f"%{user_query_text.lower()}%"
            query = query.filter(or_(func.lower(User.email).like(pattern), func.lower(User.username).like(pattern)))
        if user_role_filter == "admin":
            query = query.filter(User.is_admin.is_(True))
        elif user_role_filter == "member":
            query = query.filter(User.is_admin.is_(False))
        rows = [
            {
                "id": u.id, "email": u.email, "username": u.username,
                "is_admin": u.is_admin, "deactivated_at": u.deactivated_at,
                "created_at": u.created_at,
            }
            for u in query.order_by(User.created_at.desc()).limit(ADMIN_CSV_EXPORT_ROW_LIMIT).all()
        ]
        if len(rows) >= ADMIN_CSV_EXPORT_ROW_LIMIT:
            flash(f"Export truncated to the first {ADMIN_CSV_EXPORT_ROW_LIMIT} matching rows.", "warning")
        return _csv_response(rows, ["id", "email", "username", "is_admin", "deactivated_at", "created_at"], "users.csv")

    @app.route("/admin/content/export.csv")
    @login_required
    def admin_content_export():
        require_admin()
        paper_query_text = (request.args.get("paper_q") or "").strip()
        paper_visibility_filter = (request.args.get("paper_visibility") or "all").strip().lower()
        paper_status_filter = (request.args.get("paper_status") or "all").strip().lower()
        query = Paper.query.options(selectinload(Paper.author))
        if paper_query_text:
            pattern = f"%{paper_query_text.lower()}%"
            query = query.outerjoin(User, Paper.user_id == User.id).filter(
                or_(func.lower(Paper.title).like(pattern), func.lower(Paper.slug).like(pattern), func.lower(User.email).like(pattern))
            )
        if paper_visibility_filter == "public":
            query = query.filter(Paper.is_public.is_(True))
        elif paper_visibility_filter == "private":
            query = query.filter(Paper.is_public.is_(False))
        if paper_status_filter == "active":
            query = query.filter(or_(Paper.status.is_(None), Paper.status != "deleted"))
        elif paper_status_filter == "deleted":
            query = query.filter(Paper.status == "deleted")
        rows = [
            {
                "id": p.id, "title": p.title, "slug": p.slug, "owner_email": (p.author.email if p.author else None),
                "is_public": p.is_public, "status": p.status, "field": p.field, "year": p.year,
                "created_at": p.created_at,
            }
            for p in query.order_by(Paper.created_at.desc()).limit(ADMIN_CSV_EXPORT_ROW_LIMIT).all()
        ]
        if len(rows) >= ADMIN_CSV_EXPORT_ROW_LIMIT:
            flash(f"Export truncated to the first {ADMIN_CSV_EXPORT_ROW_LIMIT} matching rows.", "warning")
        return _csv_response(rows, ["id", "title", "slug", "owner_email", "is_public", "status", "field", "year", "created_at"], "publications.csv")

    @app.route("/admin/revenue/export.csv")
    @login_required
    def admin_revenue_export():
        require_admin()
        pay_status_filter = (request.args.get("pay_status") or "all").strip().lower()
        pay_provider_filter = (request.args.get("provider") or "all").strip().lower()
        pay_query_text = (request.args.get("pay_q") or "").strip()
        query = Payment.query.options(selectinload(Payment.user))
        if pay_status_filter in {"pending", "paid", "failed", "refunded"}:
            query = query.filter(Payment.status == pay_status_filter)
        if pay_provider_filter in {"manual", "paytr"}:
            query = query.filter(Payment.provider == pay_provider_filter)
        if pay_query_text:
            pattern = f"%{pay_query_text.lower()}%"
            query = query.outerjoin(User, Payment.user_id == User.id).filter(
                or_(func.lower(Payment.invoice_number).like(pattern), func.lower(Payment.provider_reference).like(pattern), func.lower(User.email).like(pattern))
            )
        rows = [
            {
                "id": p.id, "invoice_number": p.invoice_number, "user_email": (p.user.email if p.user else None),
                "amount_major": p.amount_kurus / 100.0, "currency": p.currency, "status": p.status,
                "provider": p.provider, "provider_reference": p.provider_reference,
                "created_at": p.created_at, "paid_at": p.paid_at,
            }
            for p in query.order_by(Payment.created_at.desc()).limit(ADMIN_CSV_EXPORT_ROW_LIMIT).all()
        ]
        if len(rows) >= ADMIN_CSV_EXPORT_ROW_LIMIT:
            flash(f"Export truncated to the first {ADMIN_CSV_EXPORT_ROW_LIMIT} matching rows.", "warning")
        return _csv_response(
            rows,
            ["id", "invoice_number", "user_email", "amount_major", "currency", "status", "provider", "provider_reference", "created_at", "paid_at"],
            "payments.csv",
        )

    @app.route("/admin/analytics/export.csv")
    @login_required
    def admin_analytics_export():
        require_admin()
        snapshot = analytics_snapshot(days=30)
        rows = [{
            "period_days": snapshot["days"],
            "model_views": snapshot["views"],
            "unique_visitors": snapshot["unique_visitors"],
            "qr_scans": snapshot["qr_scans"],
            "review_visits": snapshot["review_visits"],
            "ar_starts": snapshot["ar_starts"],
            "projects_created": snapshot["projects_created"],
            "models_uploaded": snapshot["models_uploaded"],
            "active_creators": snapshot["active_creators"],
            "conversions_completed": snapshot["conversion_completed"],
            "conversions_failed": snapshot["conversion_failed"],
        }]
        return _csv_response(rows, list(rows[0]), "analytics_30_day_summary.csv")

    @app.route("/admin/logs/export.csv")
    @login_required
    def admin_logs_export():
        require_admin()
        audit_event_filter = (request.args.get("audit_event") or "all").strip().lower()
        audit_query_text = (request.args.get("audit_q") or "").strip()
        audit_user_filter = (request.args.get("audit_user") or "").strip()
        query = AuditLog.query
        if audit_event_filter != "all":
            query = query.filter(AuditLog.event_type == audit_event_filter)
        if audit_user_filter.isdigit():
            query = query.filter(AuditLog.user_id == int(audit_user_filter))
        if audit_query_text:
            pattern = f"%{audit_query_text.lower()}%"
            query = query.filter(
                or_(func.lower(AuditLog.event_type).like(pattern), func.lower(AuditLog.resource_id).like(pattern), func.lower(AuditLog.ip_address).like(pattern))
            )
        rows = [
            {
                "id": a.id, "event_type": a.event_type, "user_id": a.user_id, "resource_id": a.resource_id,
                "ip_address": a.ip_address, "timestamp": a.timestamp,
            }
            for a in query.order_by(AuditLog.timestamp.desc()).limit(ADMIN_CSV_EXPORT_ROW_LIMIT).all()
        ]
        if len(rows) >= ADMIN_CSV_EXPORT_ROW_LIMIT:
            flash(f"Export truncated to the first {ADMIN_CSV_EXPORT_ROW_LIMIT} matching rows.", "warning")
        return _csv_response(rows, ["id", "event_type", "user_id", "resource_id", "ip_address", "timestamp"], "audit_log.csv")

    @app.route("/admin/users/<int:user_id>")
    @login_required
    def admin_user_detail(user_id):
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        papers = active_paper_query().filter_by(user_id=user.id).order_by(Paper.created_at.desc()).all()
        latest_models = (
            Model3D.query
            .join(Paper)
            .options(selectinload(Model3D.paper))
            .filter(Model3D.user_id == user.id, Paper.deleted_at.is_(None))
            .order_by(Model3D.created_at.desc())
            .limit(6)
            .all()
        )
        models = Model3D.query.filter_by(user_id=user.id).order_by(Model3D.created_at.desc()).all()
        payments = Payment.query.filter_by(user_id=user.id).order_by(Payment.created_at.desc()).all()
        audit_events = AuditLog.query.filter_by(user_id=user.id).order_by(AuditLog.timestamp.desc()).limit(50).all()
        total_spent = sum(payment.amount_kurus for payment in payments if payment.status == "paid")
        log_audit("admin_user_detail_viewed", user_id=current_user.id, resource_id=str(user.id))
        return render_template(
            "admin/user_detail.html",
            user=user,
            papers=papers,
            models=models,
            payments=payments,
            audit_events=audit_events,
            total_spent=total_spent,
            active_page="users",
        )

    @app.route("/admin/users/<int:user_id>/dashboard")
    @login_required
    def admin_user_dashboard(user_id):
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        papers = active_paper_query().filter_by(user_id=user.id).order_by(Paper.created_at.desc()).all()
        latest_models = (
            Model3D.query.join(Paper).options(selectinload(Model3D.paper))
            .filter(Model3D.user_id == user.id, Paper.deleted_at.is_(None))
            .order_by(Model3D.created_at.desc()).limit(6).all()
        )
        log_audit("admin_user_dashboard_viewed", user_id=current_user.id, resource_id=str(user.id))
        return render_template(
            "dashboard.html",
            papers=papers,
            latest_models=latest_models,
            admin_view=True,
            viewed_user=user,
        )

    def _institution_or_404(institution_id):
        institution = db.session.get(Institution, institution_id)
        if institution is None:
            abort(404)
        return institution

    def _normalize_institution_domains(raw: str) -> str | None:
        """Lowercase, strip whitespace and leading '@', dedupe, keep order."""
        seen = []
        for piece in (raw or "").split(","):
            domain = piece.strip().lower().lstrip("@")
            if domain and domain not in seen:
                seen.append(domain)
        return ", ".join(seen) or None

    def _parse_institution_date(raw):
        raw = (raw or "").strip()
        if not raw:
            return None
        for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        raise ValueError(raw)

    def _institution_form_values():
        """Parse and validate the shared create/update institution form.
        Returns (values, error_message)."""
        name = (request.form.get("name") or "").strip()[:200]
        if not name:
            return None, "Institution name is required."
        try:
            starts_at = _parse_institution_date(request.form.get("contract_starts_at"))
            ends_at = _parse_institution_date(request.form.get("contract_ends_at"))
        except ValueError:
            return None, "Contract dates must be valid (YYYY-MM-DD)."
        if starts_at and ends_at and ends_at < starts_at:
            return None, "Contract end cannot be before its start."
        price_raw = (request.form.get("annual_price") or "").strip()
        annual_price_cents = None
        if price_raw:
            try:
                annual_price_major = float(price_raw)
            except ValueError:
                return None, "Annual price must be a number."
            if annual_price_major < 0:
                return None, "Annual price cannot be negative."
            annual_price_cents = int(round(annual_price_major * 100))
        currency = (request.form.get("currency") or "TRY").strip().upper()[:3] or "TRY"
        quota_models_raw = (request.form.get("quota_model_count") or "").strip()
        quota_model_count = None
        if quota_models_raw:
            if not quota_models_raw.isdigit():
                return None, "Model quota must be a whole number."
            quota_model_count = int(quota_models_raw)
        quota_storage_raw = (request.form.get("quota_storage_mb") or "").strip()
        quota_storage_bytes = None
        if quota_storage_raw:
            try:
                quota_storage_mb = float(quota_storage_raw)
            except ValueError:
                return None, "Storage quota must be a number of MB."
            if quota_storage_mb < 0:
                return None, "Storage quota cannot be negative."
            quota_storage_bytes = int(quota_storage_mb * 1024 * 1024)
        return {
            "name": name,
            "email_domains": _normalize_institution_domains(request.form.get("email_domains") or ""),
            "contract_starts_at": starts_at,
            "contract_ends_at": ends_at,
            "annual_price_cents": annual_price_cents,
            "currency": currency,
            "quota_model_count": quota_model_count,
            "quota_storage_bytes": quota_storage_bytes,
            "notes": (request.form.get("notes") or "").strip()[:5000] or None,
            "public_description": (request.form.get("public_description") or "").strip()[:1000] or None,
        }, None

    @app.route("/admin/institutions/<int:institution_id>")
    @login_required
    def admin_institution_detail(institution_id):
        require_admin()
        institution = _institution_or_404(institution_id)
        members = (
            InstitutionMember.query.options(selectinload(InstitutionMember.user))
            .filter_by(institution_id=institution.id)
            .order_by(InstitutionMember.joined_at.asc())
            .all()
        )
        invites = (
            InstitutionInvite.query.filter_by(institution_id=institution.id)
            .order_by(InstitutionInvite.created_at.desc())
            .all()
        )
        payments = (
            Payment.query.filter_by(institution_id=institution.id)
            .order_by(Payment.created_at.desc())
            .all()
        )
        model_count, bytes_used = institution_usage(institution.id)
        page = max(request.args.get("page", type=int) or 1, 1)
        funded_models_pagination = (
            Model3D.query.options(selectinload(Model3D.paper))
            .filter(Model3D.institution_id == institution.id)
            .order_by(Model3D.created_at.desc())
            .paginate(page=page, per_page=ADMIN_PER_PAGE, error_out=False)
        )
        log_audit("admin_institution_detail_viewed", user_id=current_user.id, resource_id=str(institution.id))
        return render_template(
            "admin/institution_detail.html",
            institution=institution,
            members=members,
            invites=invites,
            payments=payments,
            usage_models=model_count,
            usage_bytes=bytes_used,
            funded_models_pagination=funded_models_pagination,
            active_page="institutions",
        )

    @app.route("/admin/institutions/create", methods=["POST"])
    @login_required
    def admin_institution_create():
        require_admin()
        values, error = _institution_form_values()
        if error:
            flash(error, "danger")
            return redirect(url_for("admin_dashboard", admin_page="institutions"))
        existing = Institution.query.filter(func.lower(Institution.name) == values["name"].lower()).first()
        if existing is not None:
            flash("An institution with that name already exists.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="institutions"))
        institution = Institution(slug=make_institution_slug(values["name"]), **values)
        db.session.add(institution)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not create the institution. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="institutions"))
        log_audit(
            "institution_created",
            user_id=current_user.id,
            resource_id=str(institution.id),
            details={"name": institution.name},
        )
        flash("Institution created.", "success")
        return redirect(url_for("admin_institution_detail", institution_id=institution.id))

    @app.route("/admin/institutions/<int:institution_id>/update", methods=["POST"])
    @login_required
    def admin_institution_update(institution_id):
        require_admin()
        institution = _institution_or_404(institution_id)
        values, error = _institution_form_values()
        if error:
            flash(error, "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        duplicate = Institution.query.filter(
            func.lower(Institution.name) == values["name"].lower(), Institution.id != institution.id
        ).first()
        if duplicate is not None:
            flash("An institution with that name already exists.", "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        previous_end = institution.contract_ends_at
        new_end = values.pop("contract_ends_at")
        for field, value in values.items():
            setattr(institution, field, value)
        if not institution.slug:
            # Self-heal rows created before the showcase slug existed.
            institution.slug = make_institution_slug(institution.name)
        models_updated = 0
        if new_end != previous_end:
            # Contract end drives every funded model's access window; the bulk
            # update must never be forgotten, so it lives in the same helper.
            models_updated = renew_institution_contract(institution, new_end)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update the institution. Please try again.", "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        log_audit(
            "institution_updated",
            user_id=current_user.id,
            resource_id=str(institution.id),
            details={
                "old_contract_ends_at": previous_end.isoformat() if previous_end else None,
                "new_contract_ends_at": new_end.isoformat() if new_end else None,
                "models_updated": models_updated,
            },
        )
        if new_end != previous_end:
            flash(f"Institution updated. Access window refreshed on {models_updated} model(s).", "success")
        else:
            flash("Institution updated.", "success")
        return redirect(url_for("admin_institution_detail", institution_id=institution.id))

    @app.route("/admin/institutions/<int:institution_id>/status", methods=["POST"])
    @login_required
    def admin_institution_status(institution_id):
        require_admin()
        institution = _institution_or_404(institution_id)
        new_status = (request.form.get("status") or "").strip().lower()
        if new_status not in {"active", "suspended"}:
            flash("Invalid institution status.", "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        previous = institution.status
        institution.status = new_status
        db.session.commit()
        log_audit(
            "institution_status_changed",
            user_id=current_user.id,
            resource_id=str(institution.id),
            details={"from": previous, "to": new_status},
        )
        flash(f"Institution {new_status}.", "success")
        return redirect(url_for("admin_institution_detail", institution_id=institution.id))

    @app.route("/admin/institutions/<int:institution_id>/end-access", methods=["POST"])
    @login_required
    def admin_institution_end_access(institution_id):
        require_admin()
        institution = _institution_or_404(institution_id)
        expired = end_institution_access_now(institution, datetime.now(UTC))
        db.session.commit()
        log_audit(
            "institution_access_ended",
            user_id=current_user.id,
            resource_id=str(institution.id),
            details={"models_expired": expired},
        )
        flash(f"Access ended now on {expired} model(s).", "success")
        return redirect(url_for("admin_institution_detail", institution_id=institution.id))

    @app.route("/admin/institutions/<int:institution_id>/admins", methods=["POST"])
    @login_required
    def admin_institution_admin_assign(institution_id):
        require_admin()
        institution = _institution_or_404(institution_id)
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter(func.lower(User.email) == email).first() if email else None
        if user is None:
            flash("No account found with that email.", "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        membership = InstitutionMember.query.filter_by(user_id=user.id).first()
        if membership is not None and membership.institution_id != institution.id:
            flash("That user already belongs to another institution.", "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        if membership is None:
            membership = InstitutionMember(institution_id=institution.id, user_id=user.id, role="admin")
            db.session.add(membership)
        else:
            membership.role = "admin"
        db.session.commit()
        log_audit(
            "institution_admin_assigned",
            user_id=current_user.id,
            resource_id=str(institution.id),
            details={"member_user_id": user.id},
        )
        flash(f"{user.email} is now an institution admin.", "success")
        return redirect(url_for("admin_institution_detail", institution_id=institution.id))

    @app.route("/admin/institutions/<int:institution_id>/members/<int:member_id>/remove", methods=["POST"])
    @login_required
    def admin_institution_member_remove(institution_id, member_id):
        require_admin()
        institution = _institution_or_404(institution_id)
        member = InstitutionMember.query.filter_by(id=member_id, institution_id=institution.id).first()
        if member is None:
            abort(404)
        removed_user_id = member.user_id
        db.session.delete(member)
        db.session.commit()
        log_audit(
            "institution_member_removed",
            user_id=current_user.id,
            resource_id=str(institution.id),
            details={"member_user_id": removed_user_id, "removed_by": "platform_admin"},
        )
        flash("Member removed from the institution.", "success")
        return redirect(url_for("admin_institution_detail", institution_id=institution.id))

    @app.route("/admin/institutions/<int:institution_id>/payments", methods=["POST"])
    @login_required
    def admin_institution_payment_create(institution_id):
        require_admin()
        institution = _institution_or_404(institution_id)
        amount_raw = (request.form.get("amount") or "").strip()
        try:
            amount_major = float(amount_raw)
        except ValueError:
            flash("Amount must be a number.", "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        if amount_major <= 0:
            flash("Amount must be greater than zero.", "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        amount_minor = int(round(amount_major * 100))
        currency = (request.form.get("currency") or institution.currency or "TRY").strip().upper()[:3]
        status_value = (request.form.get("status") or "pending").strip().lower()
        if status_value not in {"pending", "paid"}:
            flash("Institution payments can only be created as pending or paid.", "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        payment = Payment(
            institution_id=institution.id,
            plan_key="institutional",
            amount_kurus=amount_minor,
            currency=currency,
            provider="manual",
            provider_reference=(request.form.get("reference") or "").strip()[:200] or None,
            status=status_value,
        )
        if status_value == "paid":
            payment.paid_at = datetime.now(UTC)
        db.session.add(payment)
        try:
            db.session.flush()
            if status_value == "paid":
                payment.invoice_number = build_invoice_number(payment.id)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not record the payment. Please try again.", "danger")
            return redirect(url_for("admin_institution_detail", institution_id=institution.id))
        log_audit(
            "institution_payment_created",
            user_id=current_user.id,
            resource_id=str(payment.id),
            details={"institution_id": institution.id, "status": status_value, "amount_kurus": amount_minor},
        )
        flash("Institution payment recorded.", "success")
        return redirect(url_for("admin_institution_detail", institution_id=institution.id))

    @app.route("/admin/institutions/<int:institution_id>/logo", methods=["POST"])
    @login_required
    def admin_institution_logo_update(institution_id):
        require_admin()
        institution = _institution_or_404(institution_id)
        redirect_target = redirect(url_for("admin_institution_detail", institution_id=institution.id))
        if request.form.get("remove") == "1":
            if institution.logo_path:
                old = os.path.join(app.config["INSTITUTION_LOGO_FOLDER"], os.path.basename(institution.logo_path))
                institution.logo_path = None
                db.session.commit()
                cleanup_file(old)
                log_audit("institution_logo_updated", user_id=current_user.id, resource_id=str(institution.id), details={"removed": True})
            flash("Logo removed.", "success")
            return redirect_target
        file = request.files.get("logo")
        if not file or not file.filename:
            flash("Choose a logo file first.", "danger")
            return redirect_target
        ext = os.path.splitext(file.filename)[1].lower().lstrip(".")
        if ext not in ALLOWED_INSTITUTION_LOGO_EXTENSIONS:
            flash("Unsupported logo type. Use PNG, JPG, or WEBP.", "danger")
            return redirect_target
        safe = secure_filename(file.filename) or f"logo.{ext}"
        filename = f"inst{institution.id}_{uuid.uuid4().hex[:10]}_{safe}"
        dest = os.path.join(app.config["INSTITUTION_LOGO_FOLDER"], filename)
        try:
            safe_save_file(file, dest)
        except StorageError:
            flash("Could not save the logo. Please try again.", "danger")
            return redirect_target
        try:
            if os.path.getsize(dest) > MAX_INSTITUTION_LOGO_BYTES:
                os.remove(dest)
                flash("Logo is too large (max 2 MB).", "danger")
                return redirect_target
        except OSError:
            pass
        old_path = institution.logo_path
        institution.logo_path = filename
        db.session.commit()
        mirror_file(dest, f"institution_logos/{filename}")
        if old_path:
            cleanup_file(os.path.join(app.config["INSTITUTION_LOGO_FOLDER"], os.path.basename(old_path)))
        log_audit("institution_logo_updated", user_id=current_user.id, resource_id=str(institution.id), details={"filename": filename})
        flash("Logo updated.", "success")
        return redirect_target

    @app.route("/admin/institutions/<int:institution_id>/members.csv")
    @login_required
    def admin_institution_members_csv(institution_id):
        require_admin()
        institution = _institution_or_404(institution_id)
        members = (
            InstitutionMember.query.options(selectinload(InstitutionMember.user))
            .filter_by(institution_id=institution.id)
            .order_by(InstitutionMember.joined_at.asc())
            .all()
        )
        rows = [
            {
                "email": m.user.email if m.user else "",
                "username": m.user.username if m.user else "",
                "role": m.role,
                "joined_at": m.joined_at,
            }
            for m in members
        ]
        return _csv_response(rows, ["email", "username", "role", "joined_at"], f"institution_{institution.id}_members.csv")

    @app.route("/admin/ar-doctor")
    @login_required
    def admin_ar_doctor():
        """Diagnose iOS USDZ generation: is Blender on PATH and can it convert?

        Visit /admin/ar-doctor as an admin. Reports whether the `blender` binary
        is present, its version, and the result of a live GLB->USDZ conversion on
        the most recent ready model. This is how we tell, without shell access,
        whether the production container can produce the `.usdz` iOS AR needs.
        """
        require_admin()
        import shutil as _shutil
        import subprocess as _subprocess
        import tempfile as _tempfile

        report = {
            "blender_on_path": _shutil.which("blender"),
            "blender_version": None,
            "blender_python": None,
            "test_model_id": None,
            "glb_found": False,
            "conversion_ok": None,
            "conversion_stderr": None,
        }

        if report["blender_on_path"]:
            try:
                vproc = _subprocess.run(
                    ["blender", "--version"],
                    stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
                    text=True, timeout=60,
                )
                report["blender_version"] = (vproc.stdout or vproc.stderr or "").strip().splitlines()[:2]
            except Exception as exc:  # noqa: BLE001
                report["blender_version"] = f"error running blender --version: {exc}"

            # Probe the python environment Blender actually uses, so we know
            # exactly where numpy must live (Blender's bundled python vs system).
            probe = (
                "import sys,os\n"
                "print('PREFIX', sys.prefix)\n"
                "print('PYVER', '%d.%d' % sys.version_info[:2])\n"
                "print('EXEC', sys.executable)\n"
                "bindir = os.path.join(sys.prefix, 'bin')\n"
                "print('BINDIR_EXISTS', os.path.isdir(bindir))\n"
                "print('BIN', sorted(f for f in (os.listdir(bindir) if os.path.isdir(bindir) else []) if f.startswith('python')))\n"
                "try:\n"
                "    import numpy; print('NUMPY_OK', numpy.__version__, numpy.__file__)\n"
                "except Exception as e:\n"
                "    print('NUMPY_FAIL', repr(e))\n"
            )
            try:
                pproc = _subprocess.run(
                    ["blender", "--background", "--factory-startup", "--python-expr", probe],
                    stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
                    text=True, timeout=120,
                )
                combined = (pproc.stdout or "") + "\n" + (pproc.stderr or "")
                report["blender_python"] = [
                    ln for ln in combined.splitlines()
                    if ln.startswith(("PREFIX", "PYVER", "EXEC", "BINDIR_EXISTS", "BIN", "NUMPY_"))
                ]
            except Exception as exc:  # noqa: BLE001
                report["blender_python"] = f"probe error: {exc}"

        model = (
            Model3D.query.filter(Model3D.processing_status == "ready")
            .order_by(Model3D.created_at.desc())
            .first()
        )
        if model is not None:
            report["test_model_id"] = model.id
            glb_path = os.path.join(app.config["CONVERTED_FOLDER"], model.id, "model.glb")
            if not os.path.exists(glb_path):
                ensure_local(glb_path, f"converted/{model.id}/model.glb")
            report["glb_found"] = os.path.exists(glb_path)
            if report["glb_found"] and report["blender_on_path"]:
                blender_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "tools", "blender_usdz_export.py",
                )
                with _tempfile.TemporaryDirectory() as tmp:
                    out_usdz = os.path.join(tmp, "test.usdz")
                    # Mirror convert_glb_to_usdz: feed Blender a Draco-free copy,
                    # since stored GLBs are Draco-compressed and Debian's Blender
                    # importer cannot decode Draco.
                    from converters.glb_optimize import decompress_glb
                    input_glb = glb_path
                    plain = os.path.join(tmp, "plain.glb")
                    report["decompressed"] = bool(decompress_glb(glb_path, plain))
                    if report["decompressed"]:
                        input_glb = plain
                    try:
                        cproc = _subprocess.run(
                            ["blender", "--background", "--python", blender_script,
                             "--", input_glb, out_usdz],
                            stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
                            text=True, timeout=300,
                        )
                        produced = os.path.exists(out_usdz) and os.path.getsize(out_usdz) > 0
                        report["conversion_ok"] = bool(cproc.returncode == 0 and produced)
                        report["conversion_stderr"] = (cproc.stderr or cproc.stdout or "")[-1500:]
                    except Exception as exc:  # noqa: BLE001
                        report["conversion_ok"] = False
                        report["conversion_stderr"] = f"exception: {exc}"

        return jsonify(report)

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

    @app.route("/admin/papers/<int:paper_id>/visibility", methods=["POST"])
    @login_required
    def admin_paper_visibility_update(paper_id):
        require_admin()
        paper = db.session.get(Paper, paper_id)
        if not paper:
            abort(404)
        previous = {"visibility": project_visibility(paper), "status": paper.status}
        # Accept legacy admin forms while v26 clients submit the explicit
        # three-state visibility value.
        visibility = (request.form.get("visibility") or (
            "public" if request.form.get("is_public") == "1" else "private"
        )).strip().lower()
        if visibility not in PROJECT_VISIBILITIES:
            flash("Invalid visibility value.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="content"))
        paper.visibility = visibility
        paper.is_public = visibility == "public"
        if visibility == "unlisted" and not paper.share_token:
            paper.share_token = new_project_share_token()
        new_status = (request.form.get("status") or "active").strip().lower()
        if new_status not in {"active", "deleted"}:
            flash("Invalid status value.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="content"))
        paper.status = new_status
        if paper.status == "deleted":
            paper.is_public = False
            paper.visibility = "private"
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
            details={"from": previous, "to": {"visibility": project_visibility(paper), "status": paper.status}},
        )
        flash(f"Project updated: {paper.title}.", "success")
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

    def _admin_model_redirect(next_hint, model, default_page="models"):
        """Redirect back to the models list, a model's consolidated detail
        page, or the owning user's detail page, restricted to a known-safe
        set of targets (same pattern as _admin_paper_redirect)."""
        if next_hint == "user_detail":
            return redirect(url_for("admin_user_detail", user_id=model.user_id))
        if next_hint == "model_detail":
            return redirect(url_for("admin_model_detail", model_id=model.id))
        return redirect(url_for("admin_dashboard", admin_page=default_page))

    @app.route("/admin/models/<model_id>/license", methods=["POST"])
    @login_required
    def admin_model_license_update(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        next_hint = (request.form.get("next") or "").strip()
        new_license = normalize_license_type(request.form.get("license_type"))
        previous = model.license_type
        apply_model_license_defaults(model, new_license)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return _admin_model_redirect(next_hint, model)
        log_audit(
            "admin_model_license_changed",
            user_id=current_user.id,
            resource_id=model.id,
            details={"from": previous, "to": new_license},
        )
        flash(f"Model license updated to {get_license_plan(new_license).label}.", "success")
        return _admin_model_redirect(next_hint, model)

    @app.route("/admin/pricing/<plan_key>", methods=["POST"])
    @login_required
    def admin_pricing_update(plan_key):
        require_admin()
        plan_row = LicensePlanConfig.query.filter_by(key=plan_key).first()
        if not plan_row:
            abort(404)
        label = (request.form.get("label") or "").strip()
        if not label:
            flash("Label is required.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="pricing"))
        try:
            price_major = float((request.form.get("price_usd") or "").strip())
        except ValueError:
            flash("Price must be a number.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="pricing"))
        if price_major < 0:
            flash("Price cannot be negative.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="pricing"))
        duration_raw = (request.form.get("duration_days") or "").strip()
        duration_days = None
        if duration_raw:
            try:
                duration_days = int(duration_raw)
            except ValueError:
                flash("Duration must be a whole number of days, or blank for unlimited.", "danger")
                return redirect(url_for("admin_dashboard", admin_page="pricing"))
            if duration_days <= 0:
                flash("Duration must be positive, or blank for unlimited.", "danger")
                return redirect(url_for("admin_dashboard", admin_page="pricing"))
        try:
            storage_mb = int((request.form.get("storage_limit_mb") or "").strip())
        except ValueError:
            flash("Storage limit must be a whole number of MB.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="pricing"))
        if storage_mb <= 0:
            flash("Storage limit must be positive.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="pricing"))
        is_purchasable = request.form.get("is_purchasable") == "1"

        previous = {
            "label": plan_row.label,
            "price_usd": plan_row.price_usd_cents / 100.0,
            "duration_days": plan_row.duration_days,
            "storage_limit_bytes": plan_row.storage_limit_bytes,
            "is_purchasable": plan_row.is_purchasable,
        }
        plan_row.label = label
        plan_row.price_usd_cents = int(round(price_major * 100))
        plan_row.duration_days = duration_days
        plan_row.storage_limit_bytes = storage_mb * 1024 * 1024
        plan_row.is_purchasable = is_purchasable
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update the plan. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="pricing"))

        refresh_license_plan_cache()  # this dyno reflects the edit immediately

        log_audit(
            "admin_pricing_changed",
            user_id=current_user.id,
            resource_id=plan_key,
            details={
                "from": previous,
                "to": {
                    "label": plan_row.label,
                    "price_usd": plan_row.price_usd_cents / 100.0,
                    "duration_days": plan_row.duration_days,
                    "storage_limit_bytes": plan_row.storage_limit_bytes,
                    "is_purchasable": plan_row.is_purchasable,
                },
            },
        )
        flash(f"Pricing updated for {plan_row.label}.", "success")
        return redirect(url_for("admin_dashboard", admin_page="pricing"))

    @app.route("/admin/models/<model_id>/processing", methods=["POST"])
    @login_required
    def admin_model_processing_update(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        next_hint = (request.form.get("next") or "").strip()
        new_status = (request.form.get("processing_status") or "ready").strip().lower()
        if new_status not in {"queued", "processing", "ready", "failed", "replacement_failed"}:
            flash("Invalid model processing status.", "danger")
            return _admin_model_redirect(next_hint, model)
        previous = model.processing_status
        model.processing_status = new_status
        if new_status != "failed":
            model.processing_error = None
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update. Please try again.", "danger")
            return _admin_model_redirect(next_hint, model)
        log_audit(
            "admin_model_processing_changed",
            user_id=current_user.id,
            resource_id=model.id,
            details={"from": previous, "to": new_status},
        )
        flash("Model processing status updated.", "success")
        return _admin_model_redirect(next_hint, model)

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

    def _apply_payment_license_effects(payment, previous, new_status):
        """Reconcile a payment's model license state with its money state.

        Shared by admin_payment_status_update and admin_payment_create so the
        grant-on-paid / revoke-on-refund logic never diverges between the two.
        """
        if payment.institution_id is not None and payment.model_id is None:
            # Institution contract payments (offline invoices) carry no model;
            # their license effects are managed via the institution's contract
            # dates, never through payment status flips.
            return
        if new_status == "paid" and payment.model is not None and (payment.plan_key or "") in PAID_PLAN_KEYS:
            payment.model.access_starts_at = datetime.now(UTC)
            apply_model_license_defaults(payment.model, payment.plan_key)
        elif previous == "paid" and new_status in {"refunded", "failed"} and payment.model is not None:
            apply_model_license_defaults(payment.model, "free")
            payment.model.access_expires_at = datetime.now(UTC)  # revoke access now

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
        _apply_payment_license_effects(payment, previous, new_status)
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

    @app.route("/admin/payments/<int:payment_id>/delete", methods=["POST"])
    @login_required
    def admin_payment_delete(payment_id):
        """Remove a single billing record (e.g. test-run noise).

        Deleting a Payment row does NOT change any license it granted — it only
        removes the record. Adjust access via the model's license controls if
        needed. Kept separate from status changes so an accidental "Save" never
        deletes.
        """
        require_admin()
        payment = db.session.get(Payment, payment_id)
        if not payment:
            abort(404)
        invoice = payment.invoice_number or str(payment.id)
        try:
            db.session.delete(payment)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not delete the payment. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        log_audit("admin_payment_deleted", user_id=current_user.id, resource_id=str(payment_id), details={"invoice": invoice})
        flash(f"Payment record deleted: {invoice}.", "success")
        return redirect(url_for("admin_dashboard", admin_page="revenue"))

    @app.route("/admin/payments/delete-pending", methods=["POST"])
    @login_required
    def admin_payments_delete_pending():
        """Bulk-delete every pending payment (abandoned checkouts / test noise).

        Only ``pending`` rows are touched, never paid/refunded/failed records, so
        the financial trail for money that actually moved is preserved. A pending
        row deleted while a real checkout is still in flight is harmless: the
        provider webhook reconstructs the payment when it settles.
        """
        require_admin()
        try:
            deleted = Payment.query.filter_by(status="pending").delete(synchronize_session=False)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not clear pending payments. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        log_audit("admin_payments_pending_cleared", user_id=current_user.id, details={"count": deleted})
        flash(f"Deleted {deleted} pending payment record(s).", "success")
        return redirect(url_for("admin_dashboard", admin_page="revenue"))

    @app.route("/admin/jobs/<int:job_id>/retry", methods=["POST"])
    @login_required
    def admin_job_retry(job_id):
        require_admin()
        job = db.session.get(ConversionJob, job_id)
        if not job:
            abort(404)
        if job.status not in {"failed", "cancelled"}:
            flash("Only failed or cancelled jobs can be retried.", "warning")
            return redirect(url_for("admin_dashboard", admin_page="jobs"))
        attempts_before = job.attempts
        # Reset the row so the isolated worker re-claims it. attempts MUST return
        # to 0, otherwise the worker's attempts>=max_attempts guard fails it again
        # on the next poll. The web process NEVER runs the conversion inline.
        job.status = "pending"
        job.error = None
        job.started_at = None
        job.finished_at = None
        job.attempts = 0
        is_replacement = bool((job.payload or {}).get("is_replacement"))
        if not is_replacement and job.model is not None and job.model.processing_status == "failed":
            job.model.processing_status = "queued"
            job.model.processing_error = None
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not retry the job. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="jobs"))
        log_audit(
            "admin_job_retried",
            user_id=current_user.id,
            resource_id=str(job.id),
            details={"job_type": job.job_type, "attempts_before": attempts_before},
        )
        flash("Conversion job re-queued.", "success")
        return redirect(url_for("admin_dashboard", admin_page="jobs"))

    @app.route("/admin/jobs/<int:job_id>/cancel", methods=["POST"])
    @login_required
    def admin_job_cancel(job_id):
        require_admin()
        job = db.session.get(ConversionJob, job_id)
        if not job:
            abort(404)
        # Only a pending job is safe to cancel; a processing job is owned by a
        # live worker and cancelling it would race the worker's own writes.
        if job.status != "pending":
            flash("Only pending (queued) jobs can be cancelled.", "warning")
            return redirect(url_for("admin_dashboard", admin_page="jobs"))
        job.status = "cancelled"
        job.finished_at = datetime.now(UTC)
        job.error = "Cancelled by administrator."
        is_replacement = bool((job.payload or {}).get("is_replacement"))
        if not is_replacement and job.model is not None and job.model.processing_status == "queued":
            job.model.processing_status = "failed"
            job.model.processing_error = "Conversion cancelled by an administrator."
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not cancel the job. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="jobs"))
        log_audit(
            "admin_job_cancelled",
            user_id=current_user.id,
            resource_id=str(job.id),
            details={"job_type": job.job_type},
        )
        flash("Conversion job cancelled.", "success")
        return redirect(url_for("admin_dashboard", admin_page="jobs"))

    def _admin_paper_redirect(next_hint, paper):
        """Redirect back to content list or the owner's detail page after a
        paper edit, restricted to a known-safe set of targets."""
        if next_hint == "user_detail" and paper is not None:
            return redirect(url_for("admin_user_detail", user_id=paper.user_id))
        return redirect(url_for("admin_dashboard", admin_page="content"))

    @app.route("/admin/papers/<int:paper_id>/metadata", methods=["POST"])
    @login_required
    def admin_paper_metadata_update(paper_id):
        require_admin()
        paper = db.session.get(Paper, paper_id)
        if not paper:
            abort(404)
        next_hint = (request.form.get("next") or "").strip()
        title = (request.form.get("title") or "").strip()
        if not title:
            flash("Title is required.", "danger")
            return _admin_paper_redirect(next_hint, paper)
        year_raw = (request.form.get("year") or "").strip()
        year_value = paper.year
        if year_raw:
            if year_raw.isdigit() and 1500 <= int(year_raw) <= 2100:
                year_value = int(year_raw)
            else:
                flash("Year must be a number between 1500 and 2100.", "danger")
                return _admin_paper_redirect(next_hint, paper)
        else:
            year_value = None
        # slug is intentionally NOT editable (public URL stability).
        project_type = (request.form.get("project_type") or paper.project_type or "research_project").strip()
        workflow_stage = (request.form.get("workflow_stage") or paper.workflow_stage or "in_progress").strip()
        if project_type not in PROJECT_TYPES or workflow_stage not in PROJECT_WORKFLOW_STAGES:
            flash("Invalid project type or stage.", "danger")
            return _admin_paper_redirect(next_hint, paper)
        new_values = {
            "title": title[:500],
            "authors": (request.form.get("authors") or "").strip()[:500] or None,
            "year": year_value,
            "field": (request.form.get("field") or "").strip()[:100] or None,
            "doi": (request.form.get("doi") or "").strip()[:200] or None,
            "pmid": (request.form.get("pmid") or "").strip()[:100] or None,
            "institution": (request.form.get("institution") or "").strip()[:300] or None,
            "project_type": project_type,
            "workflow_stage": workflow_stage,
        }
        changed = {}
        for attr, value in new_values.items():
            before = getattr(paper, attr)
            if before != value:
                changed[attr] = [
                    before.isoformat() if hasattr(before, "isoformat") else before,
                    value.isoformat() if hasattr(value, "isoformat") else value,
                ]
                setattr(paper, attr, value)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update the publication. Please try again.", "danger")
            return _admin_paper_redirect(next_hint, paper)
        log_audit(
            "admin_paper_metadata_changed",
            user_id=current_user.id,
            resource_id=str(paper.id),
            details={"changed": changed},
        )
        flash(f"Publication metadata updated: {paper.title}.", "success")
        return _admin_paper_redirect(next_hint, paper)

    @app.route("/admin/models/<model_id>/access-window", methods=["POST"])
    @login_required
    def admin_model_access_window_update(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        next_hint = (request.form.get("next") or "").strip()
        redirect_target = _admin_model_redirect(next_hint, model)

        def _parse_dt(raw):
            raw = (raw or "").strip()
            if not raw:
                return None
            for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(raw, fmt)
                except ValueError:
                    continue
            raise ValueError(raw)

        try:
            starts_at = _parse_dt(request.form.get("access_starts_at"))
            expires_at = _parse_dt(request.form.get("access_expires_at"))
        except ValueError:
            flash("Dates must be valid (YYYY-MM-DD or YYYY-MM-DDTHH:MM).", "danger")
            return redirect_target
        if starts_at is None:
            flash("Access start is required.", "danger")
            return redirect_target
        if expires_at is not None and expires_at < starts_at:
            flash("Access expiry cannot be before the access start.", "danger")
            return redirect_target
        previous = {
            "access_starts_at": model.access_starts_at.isoformat() if model.access_starts_at else None,
            "access_expires_at": model.access_expires_at.isoformat() if model.access_expires_at else None,
        }
        # Deliberate manual override: does NOT call apply_model_license_defaults
        # (which would recompute expiry from the plan duration). license_type and
        # storage_limit_bytes are left untouched.
        model.access_starts_at = starts_at
        model.access_expires_at = expires_at
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update the access window. Please try again.", "danger")
            return redirect_target
        log_audit(
            "admin_model_access_window_changed",
            user_id=current_user.id,
            resource_id=model.id,
            details={
                "from": previous,
                "to": {
                    "access_starts_at": starts_at.isoformat() if starts_at else None,
                    "access_expires_at": expires_at.isoformat() if expires_at else None,
                },
            },
        )
        flash("Model access window updated.", "success")
        return redirect_target

    @app.route("/admin/models/<model_id>/appearance", methods=["POST"])
    @login_required
    def admin_model_appearance_update(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        next_hint = (request.form.get("next") or "").strip()
        redirect_target = _admin_model_redirect(next_hint, model)
        ok, message, category, changes = _apply_model_appearance_change(model, request.form)
        if ok:
            log_audit("admin_model_appearance_changed", user_id=current_user.id, resource_id=model.id, details=changes)
        flash(message, category)
        return redirect_target

    @app.route("/admin/models/<model_id>/qr/regenerate", methods=["POST"])
    @login_required
    def admin_model_qr_regenerate(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        next_hint = (request.form.get("next") or "").strip()
        redirect_target = _admin_model_redirect(next_hint, model, default_page="access")
        # ensure_model_qr_link is idempotent and never rotates an existing
        # public_id, so the resolver URL and any printed QR keep working.
        ensure_model_qr_link(model)
        try:
            model.qr_code_path = generate_model_qr(model, app.config["QR_FOLDER"])
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not regenerate the QR image. Please try again.", "danger")
            return redirect_target
        except Exception:  # noqa: BLE001
            db.session.rollback()
            flash("Could not regenerate the QR image. Please try again.", "danger")
            return redirect_target
        log_audit(
            "admin_model_qr_regenerated",
            user_id=current_user.id,
            resource_id=model.id,
            details={"public_id": model.public_id},
        )
        flash("QR image regenerated.", "success")
        return redirect_target

    @app.route("/admin/models/<model_id>")
    @login_required
    def admin_model_detail(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        versions = (
            ModelVersion.query.filter_by(model_id=model_id)
            .order_by(ModelVersion.version_number.desc())
            .all()
        )
        return render_template("admin/model_detail.html", model=model, versions=versions)

    @app.route("/admin/models/<model_id>/versions")
    @login_required
    def admin_model_versions(model_id):
        # Version history is now a section on the consolidated model detail
        # page; this route survives as a redirect so any bookmarked or
        # external links to it keep working.
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        return redirect(url_for("admin_model_detail", model_id=model_id) + "#versions")

    @app.route("/admin/models/<model_id>/poster/regenerate", methods=["POST"])
    @login_required
    def admin_model_poster_regenerate(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        next_hint = (request.form.get("next") or "").strip()
        redirect_target = _admin_model_redirect(next_hint, model)
        glb_path = model.glb_path
        ensure_local(glb_path, f"converted/{model_id}/model.glb")
        poster_png = os.path.join(os.path.dirname(glb_path), "poster.png")
        from converters.poster import generate_poster

        if not generate_poster(glb_path, poster_png):
            flash("Could not regenerate the poster from this model's GLB.", "danger")
            return redirect_target
        model.poster_path = poster_png
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not save the regenerated poster. Please try again.", "danger")
            return redirect_target
        mirror_file(poster_png, f"converted/{model_id}/poster.png")
        log_audit("admin_model_poster_regenerated", user_id=current_user.id, resource_id=model.id)
        flash("Poster regenerated.", "success")
        return redirect_target

    @app.route("/admin/models/posters/generate-missing", methods=["POST"])
    @login_required
    def admin_generate_missing_posters():
        """Backfill preview images for legacy models in a bounded admin batch."""
        require_admin()
        models = (
            Model3D.query
            .filter(or_(Model3D.poster_path.is_(None), Model3D.poster_path == ""))
            .filter(Model3D.processing_status.in_(("ready", "replacement_failed")))
            .order_by(Model3D.created_at.asc())
            .limit(100)
            .all()
        )
        generated = 0
        failed = 0
        for model in models:
            try:
                directory = os.path.join(app.config["CONVERTED_FOLDER"], model.id)
                glb_path = os.path.join(directory, "model.glb")
                if not os.path.exists(glb_path):
                    ensure_local(glb_path, f"converted/{model.id}/model.glb")
                poster_png = os.path.join(directory, "poster.png")
                if not os.path.exists(glb_path) or not generate_poster(glb_path, poster_png):
                    failed += 1
                    continue
                model.poster_path = poster_png
                generated += 1
                mirror_file(poster_png, f"converted/{model.id}/poster.png")
            except Exception:
                logger.exception("Poster backfill failed for model %s", model.id)
                failed += 1
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not save generated preview records.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="models"))
        log_audit(
            "admin_model_posters_backfilled",
            user_id=current_user.id,
            details={"generated": generated, "failed": failed, "checked": len(models)},
        )
        flash(f"Generated {generated} missing preview(s)." + (f" {failed} could not be generated." if failed else ""), "success" if generated else "warning")
        return redirect(url_for("admin_dashboard", admin_page="models"))

    @app.route("/admin/models/<model_id>/mirror/retry", methods=["POST"])
    @login_required
    def admin_model_mirror_retry(model_id):
        require_admin()
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        redirect_target = redirect(url_for("admin_dashboard", admin_page="storage"))
        ok = mirror_directory_sync(
            os.path.join(app.config["CONVERTED_FOLDER"], model_id),
            f"converted/{model_id}",
        )
        model.r2_mirror_failed_at = None if ok else datetime.now(UTC)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update the mirror status. Please try again.", "danger")
            return redirect_target
        if ok:
            log_audit("admin_model_mirror_retried", user_id=current_user.id, resource_id=model.id)
            flash("R2 mirror retried successfully.", "success")
        else:
            flash("R2 mirror retry failed again. Check R2 credentials/connectivity.", "warning")
        return redirect_target

    @app.route("/admin/jobs/<int:job_id>/max-attempts", methods=["POST"])
    @login_required
    def admin_job_max_attempts_update(job_id):
        require_admin()
        job = db.session.get(ConversionJob, job_id)
        if not job:
            abort(404)
        raw = (request.form.get("max_attempts") or "").strip()
        try:
            new_max = int(raw)
        except ValueError:
            flash("Max attempts must be a whole number.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="jobs"))
        if new_max < 1 or new_max > 20:
            flash("Max attempts must be between 1 and 20.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="jobs"))
        previous = job.max_attempts
        job.max_attempts = new_max
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update max attempts. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="jobs"))
        log_audit(
            "admin_job_max_attempts_changed",
            user_id=current_user.id,
            resource_id=str(job.id),
            details={"from": previous, "to": new_max},
        )
        flash("Max attempts updated.", "success")
        return redirect(url_for("admin_dashboard", admin_page="jobs"))

    @app.route("/admin/annotations/<int:annotation_id>/delete", methods=["POST"])
    @login_required
    def admin_annotation_delete(annotation_id):
        # Admin-only moderation path: unlike model_annotation_delete (the owner
        # route), this deliberately bypasses require_model_ownership so a
        # public model's abusive/inappropriate hotspot can be removed without
        # the owner's cooperation.
        require_admin()
        annotation = db.session.get(ModelAnnotation, annotation_id)
        if not annotation:
            abort(404)
        details = {"label": annotation.label, "model_id": annotation.model_id}
        db.session.delete(annotation)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not delete the annotation. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="annotations"))
        log_audit(
            "admin_annotation_deleted",
            user_id=current_user.id,
            resource_id=str(annotation_id),
            details=details,
        )
        flash("Annotation deleted.", "success")
        return redirect(url_for("admin_dashboard", admin_page="annotations"))

    @app.route("/admin/payments/create", methods=["POST"])
    @login_required
    def admin_payment_create():
        require_admin()
        email = (request.form.get("user_email") or "").strip().lower()
        target_user = User.query.filter(func.lower(User.email) == email).first() if email else None
        if target_user is None:
            flash("No user found for that email.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        model_id = (request.form.get("model_id") or "").strip()
        model = db.session.get(Model3D, model_id) if model_id else None
        if model_id and model is None:
            flash("No model found for that id.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        plan_key = (request.form.get("plan_key") or "").strip()
        if plan_key and plan_key not in PAID_PLAN_KEYS:
            flash("Invalid plan key.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        amount_raw = (request.form.get("amount") or "").strip()
        try:
            amount_major = float(amount_raw)
        except ValueError:
            flash("Amount must be a number.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        if amount_major <= 0:
            flash("Amount must be greater than zero.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        amount_minor = int(round(amount_major * 100))
        currency = (request.form.get("currency") or app.config.get("PAYMENT_CURRENCY") or "TRY").strip().upper()[:3]
        status_value = (request.form.get("status") or "pending").strip().lower()
        if status_value not in {"pending", "paid"}:
            flash("Manual payments can only be created as pending or paid.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        payment = Payment(
            user_id=target_user.id,
            paper_id=model.paper_id if model else None,
            model_id=model.id if model else None,
            plan_key=plan_key or None,
            amount_kurus=amount_minor,
            currency=currency,
            provider="manual",
            provider_reference=(request.form.get("reference") or "").strip()[:200] or None,
            status=status_value,
        )
        if status_value == "paid":
            payment.paid_at = datetime.now(UTC)
        db.session.add(payment)
        try:
            db.session.flush()
            if status_value == "paid":
                payment.invoice_number = build_invoice_number(payment.id)
                _apply_payment_license_effects(payment, "pending", "paid")
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not create the payment. Please try again.", "danger")
            return redirect(url_for("admin_dashboard", admin_page="revenue"))
        log_audit(
            "admin_payment_created",
            user_id=current_user.id,
            resource_id=str(payment.id),
            details={"user_id": target_user.id, "model_id": payment.model_id, "status": status_value, "amount_kurus": amount_minor},
        )
        flash("Manual payment recorded.", "success")
        return redirect(url_for("admin_dashboard", admin_page="revenue"))

    @app.route("/admin/users/<int:user_id>/email", methods=["POST"])
    @login_required
    def admin_user_email_update(user_id):
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        if user.google_id:
            flash("This account is linked to Google; its email cannot be changed here.", "warning")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        new_email = (request.form.get("email") or "").strip().lower()
        if "@" not in new_email or "." not in new_email.split("@")[-1]:
            flash("Enter a valid email address.", "danger")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        clash = User.query.filter(func.lower(User.email) == new_email, User.id != user.id).first()
        if clash is not None:
            flash("Another account already uses that email.", "danger")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        previous = user.email
        if previous == new_email:
            flash("Email is unchanged.", "info")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        user.email = new_email
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not update the email. Please try again.", "danger")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        log_audit(
            "admin_user_email_changed",
            user_id=current_user.id,
            resource_id=str(user.id),
            details={"from": previous, "to": new_email},
        )
        flash("Email updated. Note: configured-admin promotion is matched by email.", "success")
        return redirect(url_for("admin_user_detail", user_id=user.id))

    @app.route("/admin/users/<int:user_id>/deactivate", methods=["POST"])
    @login_required
    def admin_user_deactivate(user_id):
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        if user.id == current_user.id:
            flash("You cannot deactivate your own account.", "warning")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        if user_is_configured_admin(user):
            flash("Configured admin accounts cannot be deactivated.", "warning")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        if user.deactivated_at is not None:
            flash("Account is already deactivated.", "info")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        user.deactivated_at = datetime.now(UTC)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not deactivate the account. Please try again.", "danger")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        log_audit("admin_user_deactivated", user_id=current_user.id, resource_id=str(user.id))
        flash(f"Account deactivated: {user.email}.", "success")
        return redirect(url_for("admin_user_detail", user_id=user.id))

    @app.route("/admin/users/<int:user_id>/reactivate", methods=["POST"])
    @login_required
    def admin_user_reactivate(user_id):
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        if user.deactivated_at is None:
            flash("Account is already active.", "info")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        user.deactivated_at = None
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Could not reactivate the account. Please try again.", "danger")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        log_audit("admin_user_reactivated", user_id=current_user.id, resource_id=str(user.id))
        flash(f"Account reactivated: {user.email}.", "success")
        return redirect(url_for("admin_user_detail", user_id=user.id))

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    @login_required
    def admin_user_delete(user_id):
        """Permanently delete a user and everything they own.

        Mirrors the self-serve /account/delete flow (files collected up front,
        financial + audit rows detached rather than destroyed, then the User is
        deleted so papers -> models cascade). Guards: an admin cannot delete
        their own account here, and configured (env ADMIN_EMAILS) admins are
        protected. This is irreversible; the reversible option is Deactivate.
        """
        require_admin()
        user = db.session.get(User, user_id)
        if not user:
            abort(404)
        if user.id == current_user.id:
            flash("You cannot delete your own account from here.", "warning")
            return redirect(url_for("admin_user_detail", user_id=user.id))
        if user_is_configured_admin(user):
            flash("Configured admin accounts cannot be deleted.", "warning")
            return redirect(url_for("admin_user_detail", user_id=user.id))

        # Collect on-disk files (GLB/USDZ/QR/poster/PDF) before the DB cascade
        # removes the rows we'd need to locate them.
        files_to_remove = []
        for paper in user.papers:
            files_to_remove.extend(collect_paper_file_paths(app, paper))

        uid = user.id
        user_email = user.email
        try:
            log_audit("admin_user_deleted", user_id=current_user.id, resource_id=str(uid), details={"email": user_email})
            # Preserve the financial + audit trail: detach these rows (nullable
            # user_id) instead of deleting them, so revenue/audit history survives.
            ConversionJob.query.filter_by(user_id=uid).update({"user_id": None})
            Payment.query.filter_by(user_id=uid).update({"user_id": None})
            AuditLog.query.filter(AuditLog.user_id == uid).update({"user_id": None})
            Paper.query.filter_by(deleted_by_user_id=uid).update({"deleted_by_user_id": None})
            # InstitutionMember.user_id is NOT NULL, so the ORM can't null it out
            # on the User cascade — remove the membership row explicitly first.
            membership = InstitutionMember.query.filter_by(user_id=uid).first()
            if membership is not None:
                db.session.delete(membership)
            db.session.delete(user)  # cascades papers -> models -> annotations/qr/versions
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Admin user deletion failed")
            flash("Could not delete the account. Please try again.", "danger")
            return redirect(url_for("admin_user_detail", user_id=uid))

        cleanup_paths(files_to_remove)
        flash(f"Account permanently deleted: {user_email}.", "success")
        return redirect(url_for("admin_dashboard", admin_page="users"))

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

    @app.route("/profile")
    @login_required
    def profile():
        # Licensing is per-model (each Model3D carries its own license_type and
        # access window), so the profile no longer sells an account-level plan.
        # Upgrades/renewals happen per model from the publication detail page and
        # the expired-viewer CTA, which route through /models/<id>/upgrade/<plan>.

        # Profile statistics. PERF-2: eager-load models to avoid N+1 in the
        # Python aggregation loops below.
        user_papers = (
            Paper.query.options(selectinload(Paper.models))
            .filter_by(user_id=current_user.id)
            .all()
        )
        paper_count = len(user_papers)
        # Licensing lives at the model level (license_type drives real AR/QR
        # access), so report the model license distribution.
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

    @app.route("/papers/new", methods=["GET", "POST"], endpoint="paper_new")
    @app.route("/projects/new", methods=["GET", "POST"], endpoint="project_new")
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
    def project_new():
        if request.method == "POST":
            paper_data, paper_errors = validate_project_form(request.form)
            if paper_errors:
                flash(" ".join(paper_errors), "danger")
                return render_template("paper_new.html", form=request.form)

            # Licensing is per-model (Model3D.license_type); papers carry no
            # plan/package and never expire on their own.
            paper = Paper(
                title=paper_data["title"],
                authors=paper_data["authors"],
                year=paper_data["year"],
                field=paper_data["field"],
                abstract=paper_data["abstract"],
                doi=paper_data["doi"],
                institution=paper_data["institution"],
                pmid=paper_data["pmid"],
                project_type=paper_data["project_type"],
                workflow_stage=paper_data["workflow_stage"],
                visibility=paper_data["visibility"],
                share_token=(new_project_share_token() if paper_data["visibility"] == "unlisted" else None),
                is_public=paper_data["is_public"],
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
            track_event("project_created", owner_user_id=current_user.id, project_id=paper.id)
            first_model_file = request.files.get("model_file") or request.files.get("model")
            if first_model_file and first_model_file.filename:
                ok, message = _create_model_for_paper(
                    paper,
                    first_model_file,
                    request.files.getlist("model_companion_files"),
                    display_name=request.form.get("model_display_name"),
                    description=request.form.get("model_description"),
                    color=request.form.get("color") if request.form.get("color_enabled") == "yes" else None,
                    source_unit=request.form.get("source_unit"),
                    compliance_confirm=request.form.get("compliance_confirm"),
                )
                category = "success" if ok else "danger"
                flash(message, category)
            else:
                flash("Project created.", "success")
            return redirect(url_for("project_detail", slug=paper.slug))

        return render_template("paper_new.html", form={})

    @app.route("/papers/<slug>", endpoint="paper_detail")
    @app.route("/projects/<slug>", endpoint="project_detail")
    @login_required
    def project_detail(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()
        if paper.user_id != current_user.id:
            abort(403)
        # After a paid upgrade the provider redirects here with ?upgraded=<model_id>.
        # Show a success banner; its wording is based on the model's ACTUAL access
        # state (not just the URL param) so a webhook that hasn't landed yet reads
        # as "activating" rather than a misleading hard "done".
        upgraded_model = None
        upgraded_active = False
        upgraded_id = request.args.get("upgraded")
        if upgraded_id:
            upgraded_model = next((m for m in paper.models if m.id == upgraded_id), None)
            if upgraded_model is not None:
                upgraded_active = model_access_status(upgraded_model) == "active"
        return render_template(
            "paper_detail.html",
            paper=paper,
            upgraded_model=upgraded_model,
            upgraded_active=upgraded_active,
        )

    @app.route("/papers/<slug>/edit", methods=["GET", "POST"], endpoint="paper_edit")
    @app.route("/projects/<slug>/edit", methods=["GET", "POST"], endpoint="project_edit")
    @login_required
    @require_paper_ownership
    def project_edit(slug):
        paper = active_paper_query().filter_by(slug=slug).first_or_404()

        if request.method == "POST":
            paper_data, paper_errors = validate_project_form(request.form)
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
            paper.project_type = paper_data["project_type"]
            paper.workflow_stage = paper_data["workflow_stage"]
            paper.visibility = paper_data["visibility"]
            if paper.visibility == "unlisted" and not paper.share_token:
                paper.share_token = new_project_share_token()
            paper.is_public = paper_data["is_public"]

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
                track_event("project_updated", owner_user_id=current_user.id, project_id=paper.id)
            except SQLAlchemyError:
                db.session.rollback()
                cleanup_file(saved_pdf_path)
                logger.exception("Paper update failed")
                flash("The publication could not be updated. Please try again.", "danger")
                return render_template("paper_new.html", form=request.form, paper=paper, mode="edit")

            cleanup_file(old_pdf_path)
            flash("Project updated.", "success")
            return redirect(url_for("project_detail", slug=paper.slug))

        form = {
            "title": paper.title or "",
            "authors": paper.authors or "",
            "year": paper.year or "",
            "field": paper.field or "",
            "institution": paper.institution or "",
            "doi": paper.doi or "",
            "pmid": paper.pmid or "",
            "abstract": paper.abstract or "",
            "project_type": paper.project_type or "research_project",
            "workflow_stage": paper.workflow_stage or "in_progress",
            "visibility": paper.visibility or ("public" if paper.is_public else "private"),
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
            display_name=request.form.get("display_name"),
            description=request.form.get("description"),
            color=request.form.get("color") if request.form.get("color_enabled") == "yes" else None,
            source_unit=request.form.get("source_unit"),
            compliance_confirm=request.form.get("compliance_confirm"),
        )
        flash(message, "success" if ok else "danger")
        return redirect(url_for("paper_detail", slug=slug))

    # NOTE: there is intentionally no owner-facing "change my model's license"
    # route. Licenses are paid upgrades only (see upgrade_model_license ->
    # provider checkout); an admin can still override a model's tier for free via
    # admin_model_license_update (/admin/models/<id>/license). Re-adding a
    # self-serve free license change here would bypass the payment flow.

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
            "source_unit": model.source_unit or "embedded",
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
        ok, message, category, changes = _apply_model_appearance_change(model, request.form)
        if ok:
            log_audit("model_appearance_updated", user_id=current_user.id, resource_id=model_id, details=changes)
        flash(message, category)
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
        if target_cm <= 0:
            flash("Target dimension must be greater than 0 cm.", "danger")
            return redirect(url_for("model_edit", model_id=model.id))

        glb_path = model.glb_path
        ensure_local(glb_path, f"converted/{model_id}/model.glb")
        backup_path = glb_path + ".rescale.bak"
        try:
            if os.path.exists(glb_path):
                shutil.copy2(glb_path, backup_path)
            try:
                import re as _re

                from converters.glb_scale import apply_uniform_scale

                # The stored GLB is Draco-compressed (trimesh can't read its
                # geometry), so derive the current longest dimension from the
                # cached dimensions_cm — set at upload from the uncompressed mesh
                # and kept current after each rescale. Accessor min/max would
                # ignore the existing scale node, so it is only a last resort.
                current_vals = [float(v) for v in _re.findall(r"[\d.]+", model.dimensions_cm or "")]
                if not current_vals:
                    measured = compute_glb_dimensions_cm(glb_path)
                    current_vals = [float(v) for v in _re.findall(r"[\d.]+", measured or "")]
                current_longest_cm = max(current_vals) if current_vals else 0.0
                if current_longest_cm <= 0:
                    raise ValueError("Cannot determine current model size")
                scale_factor = target_cm / current_longest_cm
                if not apply_uniform_scale(glb_path, scale_factor):
                    raise ValueError("Could not apply scale to GLB")
                # Scale the cached dimensions to match (avoids re-measuring the
                # Draco geometry, which trimesh cannot read).
                model.dimensions_cm = (
                    " x ".join(f"{v * scale_factor:.1f}" for v in current_vals) + " cm"
                )
            except Exception:
                logger.exception("Rescale failed; restoring backup")
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, glb_path)
                flash("Rescale failed. The previous model is still active.", "warning")
                return redirect(url_for("model_edit", model_id=model.id))

            # Regenerate the iOS USDZ from the rescaled GLB so AR Quick Look
            # matches the new size — otherwise iOS keeps the old (giant) scale.
            usdz_path = os.path.join(os.path.dirname(glb_path), "model.usdz")
            usdz_ok = False
            try:
                usdz_ok = convert_glb_to_usdz(glb_path, usdz_path)
            except Exception:
                logger.exception("USDZ regeneration after rescale failed; iOS AR may keep old scale")

            poster_png = os.path.join(os.path.dirname(glb_path), "poster.png")
            if generate_poster(glb_path, poster_png):
                model.poster_path = poster_png
            db.session.commit()
            # Re-mirror the rescaled GLB, USDZ, and refreshed poster to R2.
            mirror_file(glb_path, f"converted/{model_id}/model.glb")
            if usdz_ok and os.path.exists(usdz_path):
                mirror_file(usdz_path, f"converted/{model_id}/model.usdz")
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

    @app.route("/models/<model_id>/change-unit", methods=["POST"])
    @login_required
    @require_model_ownership
    def model_change_unit(model_id):
        """Re-interpret the model's source unit (mm/cm/m), rescaling the GLB by
        the ratio between the old and new unit. For STL/OBJ models that declared
        a unit at upload — the user can fix a wrong choice here."""
        model = db.session.get(Model3D, model_id)
        if not model:
            abort(404)
        units = {"mm": 0.001, "cm": 0.01, "m": 1.0}
        new_unit = (request.form.get("source_unit") or "").strip().lower()
        old_unit = (model.source_unit or "").strip().lower()
        if new_unit not in units:
            flash("Choose a valid unit (mm, cm, or m).", "danger")
            return redirect(url_for("model_edit", model_id=model.id))
        if old_unit not in units:
            flash("Unit change is only available for STL/OBJ models that declared a unit. Use “Resize model” to set the exact size instead.", "warning")
            return redirect(url_for("model_edit", model_id=model.id))
        if new_unit == old_unit:
            flash(f"The model is already in {new_unit}.", "info")
            return redirect(url_for("model_edit", model_id=model.id))

        factor = units[new_unit] / units[old_unit]
        glb_path = model.glb_path
        ensure_local(glb_path, f"converted/{model_id}/model.glb")
        backup_path = glb_path + ".unit.bak"
        try:
            if os.path.exists(glb_path):
                shutil.copy2(glb_path, backup_path)
            try:
                import re as _re

                from converters.glb_scale import apply_uniform_scale

                if not apply_uniform_scale(glb_path, factor):
                    raise ValueError("Could not apply scale to GLB")
                vals = [float(v) for v in _re.findall(r"[\d.]+", model.dimensions_cm or "")]
                if vals:
                    model.dimensions_cm = " x ".join(f"{v * factor:.1f}" for v in vals) + " cm"
                model.source_unit = new_unit
            except Exception:
                logger.exception("Unit change failed; restoring backup")
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, glb_path)
                flash("Unit change failed. The previous model is still active.", "warning")
                return redirect(url_for("model_edit", model_id=model.id))

            usdz_path = os.path.join(os.path.dirname(glb_path), "model.usdz")
            usdz_ok = False
            try:
                usdz_ok = convert_glb_to_usdz(glb_path, usdz_path)
            except Exception:
                logger.exception("USDZ regeneration after unit change failed")
            poster_png = os.path.join(os.path.dirname(glb_path), "poster.png")
            if generate_poster(glb_path, poster_png):
                model.poster_path = poster_png
            db.session.commit()
            mirror_file(glb_path, f"converted/{model_id}/model.glb")
            if usdz_ok and os.path.exists(usdz_path):
                mirror_file(usdz_path, f"converted/{model_id}/model.usdz")
            if model.poster_path:
                mirror_file(poster_png, f"converted/{model_id}/poster.png")
            log_audit(
                "model_unit_changed",
                user_id=current_user.id,
                resource_id=model_id,
                details={"from": old_unit, "to": new_unit},
            )
            flash(f"Source unit changed to {new_unit}.", "success")
        except SQLAlchemyError:
            db.session.rollback()
            if os.path.exists(backup_path):
                try:
                    shutil.copy2(backup_path, glb_path)
                except OSError:
                    logger.exception("Failed to restore unit-change backup after DB error")
            flash("Could not change the unit.", "danger")
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
        # Optional camera angle the note was placed from (model-viewer strings).
        camera = data.get("camera") or {}
        camera_orbit = (str(camera.get("orbit")).strip()[:64] or None) if camera.get("orbit") else None
        camera_target = (str(camera.get("target")).strip()[:96] or None) if camera.get("target") else None
        camera_fov = (str(camera.get("fov")).strip()[:16] or None) if camera.get("fov") else None
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
                camera_orbit=camera_orbit,
                camera_target=camera_target,
                camera_fov=camera_fov,
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
            # Stay on the edit page so the user can keep tweaking the model
            # instead of being bounced back to the publication page.
            return redirect(url_for("model_edit", model_id=model.id))
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
