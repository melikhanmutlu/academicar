"""
Application configuration loaded from environment variables.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


class Config:
    APP_ENV = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).lower()
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")
    DEBUG = os.environ.get("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    WTF_CSRF_TIME_LIMIT = None
    PREFERRED_URL_SCHEME = "https" if APP_ENV in {"production", "prod", "pilot"} else "http"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = APP_ENV in {"production", "prod", "pilot"}

    # Database: PostgreSQL on Railway/production via DATABASE_URL, else SQLite.
    _db_url = os.environ.get("DATABASE_URL", "").strip()
    if _db_url:
        # Heroku/Railway compatibility: postgres:// -> postgresql://
        if _db_url.startswith("postgres://"):
            _db_url = _db_url.replace("postgres://", "postgresql://", 1)
        SQLALCHEMY_DATABASE_URI = _db_url
    else:
        instance_dir = BASE_DIR / "instance"
        instance_dir.mkdir(exist_ok=True)
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{instance_dir / 'academic_ar.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Runtime folders. Railway filesystem is ephemeral; use external object
    # storage before relying on these paths for long-lived production files.
    UPLOAD_FOLDER = str(BASE_DIR / os.environ.get("UPLOAD_FOLDER", "uploads"))
    CONVERTED_FOLDER = str(BASE_DIR / os.environ.get("CONVERTED_FOLDER", "converted"))
    QR_FOLDER = str(BASE_DIR / os.environ.get("QR_FOLDER", "qr_codes"))
    PDF_FOLDER = str(BASE_DIR / os.environ.get("PDF_FOLDER", "pdfs"))

    # Upload limits.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 50 * 1024 * 1024))
    ALLOWED_STL_EXTENSIONS = {"stl"}
    ALLOWED_PDF_EXTENSIONS = {"pdf"}
    UPLOAD_RATE_LIMIT_COUNT = int(os.environ.get("UPLOAD_RATE_LIMIT_COUNT", 5))
    UPLOAD_RATE_LIMIT_WINDOW = int(os.environ.get("UPLOAD_RATE_LIMIT_WINDOW", 600))

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
        ):
            os.makedirs(folder, exist_ok=True)
