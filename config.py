"""
Application configuration loaded from environment variables.
"""
import os
from datetime import timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return False

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_APP_ENV = (
    "production"
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PUBLIC_DOMAIN")
    else os.environ.get("FLASK_ENV", "development")
)
DEFAULT_STORAGE_ROOT = (
    os.environ.get("STORAGE_ROOT")
    or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    or str(BASE_DIR / "storage")
)


def runtime_folder(env_name: str, default_name: str, runtime_base: Path) -> str:
    configured = os.environ.get(env_name)
    path = Path(configured) if configured else runtime_base / default_name
    if not path.is_absolute():
        path = runtime_base / path
    return str(path)


class Config:
    APP_ENV = os.environ.get("APP_ENV", DEFAULT_APP_ENV).lower()
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    DEBUG = os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    # CSRF tokens expire after 1h; long-lived tokens widen the CSRF window
    # unnecessarily. Forms re-render whenever a user keeps a tab open.
    WTF_CSRF_TIME_LIMIT = int(os.environ.get("WTF_CSRF_TIME_LIMIT", 3600))
    PREFERRED_URL_SCHEME = "https" if APP_ENV in {"production", "prod", "pilot"} else "http"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV in {"production", "prod", "pilot"}
    PERMANENT_SESSION_LIFETIME = timedelta(days=int(os.environ.get("SESSION_LIFETIME_DAYS", 14)))
    # Single source of truth for password length minimum (auth + account flows).
    PASSWORD_MIN_LENGTH = int(os.environ.get("PASSWORD_MIN_LENGTH", 8))

    # Database: PostgreSQL on Railway/production via DATABASE_URL, else SQLite.
    _db_url = os.environ.get("DATABASE_URL", "").strip()
    if _db_url:
        # Heroku/Railway compatibility and psycopg v3 integration
        if _db_url.startswith("postgres://"):
            _db_url = _db_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif _db_url.startswith("postgresql://"):
            _db_url = _db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        SQLALCHEMY_DATABASE_URI = _db_url
    else:
        instance_dir = BASE_DIR / "instance"
        instance_dir.mkdir(exist_ok=True)
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance_dir / 'academic_ar.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Redis is used in production for shared rate limits. Local development
    # defaults to memory storage so the app runs without a local Redis daemon.
    REDIS_URL = os.environ.get("REDIS_URL")
    RATELIMIT_STORAGE_URI = os.environ.get(
        "RATELIMIT_STORAGE_URI",
        REDIS_URL if REDIS_URL else "memory://",
    )

    # Runtime folders. Railway filesystem is ephemeral; use external object
    # storage before relying on these paths for long-lived production files.
    STORAGE_PROVIDER = os.environ.get("STORAGE_PROVIDER", "railway_volume")
    STORAGE_ROOT = DEFAULT_STORAGE_ROOT
    _runtime_base = Path(STORAGE_ROOT) if APP_ENV in {"production", "prod", "pilot"} else BASE_DIR
    UPLOAD_FOLDER = runtime_folder("UPLOAD_FOLDER", "uploads", _runtime_base)
    CONVERTED_FOLDER = runtime_folder("CONVERTED_FOLDER", "converted", _runtime_base)
    QR_FOLDER = runtime_folder("QR_FOLDER", "qr_codes", _runtime_base)
    PDF_FOLDER = runtime_folder("PDF_FOLDER", "pdfs", _runtime_base)

    # Upload limits.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 260 * 1024 * 1024))
    ALLOWED_STL_EXTENSIONS = {"stl"}
    ALLOWED_PDF_EXTENSIONS = {"pdf"}
    UPLOAD_RATE_LIMIT_COUNT = int(os.environ.get("UPLOAD_RATE_LIMIT_COUNT", 5))
    UPLOAD_RATE_LIMIT_WINDOW = int(os.environ.get("UPLOAD_RATE_LIMIT_WINDOW", 600))
    if APP_ENV in {"production", "prod", "pilot"}:
        DEV_INLINE_JOBS = os.environ.get("ALLOW_PRODUCTION_INLINE_JOBS", "0").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    else:
        DEV_INLINE_JOBS = os.environ.get("DEV_INLINE_JOBS", "1").lower() in {"1", "true", "yes", "on"}

    # Compliance & Legal
    TERMS_VERSION = os.environ.get("TERMS_VERSION", "1.0")

    # Google OAuth.
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

    # Site URL for OAuth callbacks and generated public links.
    SITE_URL = os.environ.get(
        "SITE_URL",
        os.environ.get("RAILWAY_PUBLIC_DOMAIN", "http://localhost:5000"),
    )
    if SITE_URL and not SITE_URL.startswith(("http://", "https://")):
        SITE_URL = f"https://{SITE_URL}"

    @staticmethod
    def init_app(app):
        for folder in (
            app.config["UPLOAD_FOLDER"],
            app.config["CONVERTED_FOLDER"],
            app.config["QR_FOLDER"],
            app.config["PDF_FOLDER"],
            app.config["STORAGE_ROOT"],
        ):
            os.makedirs(folder, exist_ok=True)
