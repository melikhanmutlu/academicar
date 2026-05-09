"""
SQLAlchemy database models: User, Paper, Model3D.
"""
from datetime import UTC, datetime, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def utc_now():
    return datetime.now(UTC)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)  # NULL for Google-only users
    google_id = db.Column(db.String(100), unique=True, nullable=True, index=True)
    avatar_url = db.Column(db.String(500), nullable=True)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    plan = db.Column(db.String(30), nullable=False, default="free")
    created_at = db.Column(db.DateTime, default=utc_now)

    papers = db.relationship(
        "Paper", backref="author", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class Paper(db.Model):
    __tablename__ = "papers"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    authors = db.Column(db.String(500), nullable=True)
    year = db.Column(db.Integer, nullable=True)
    field = db.Column(db.String(100), nullable=True)
    abstract = db.Column(db.Text, nullable=True)
    doi = db.Column(db.String(200), nullable=True)
    institution = db.Column(db.String(300), nullable=True)
    pdf_path = db.Column(db.String(500), nullable=True)
    slug = db.Column(db.String(250), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    package_type = db.Column(db.String(30), nullable=False, default="temporary")
    status = db.Column(db.String(30), nullable=False, default="active")
    is_public = db.Column(db.Boolean, nullable=False, default=False)
    payment_status = db.Column(db.String(30), nullable=False, default="free")
    payment_provider = db.Column(db.String(50), nullable=True)
    payment_reference = db.Column(db.String(200), nullable=True)
    pmid = db.Column(db.String(100), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True, default=lambda: utc_now() + timedelta(days=3))
    created_at = db.Column(db.DateTime, default=utc_now)

    models = db.relationship(
        "Model3D", backref="paper", lazy=True, cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Paper {self.title[:40]}>"


class Model3D(db.Model):
    __tablename__ = "models"

    id = db.Column(db.String(36), primary_key=True)  # UUID
    paper_id = db.Column(db.Integer, db.ForeignKey("papers.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    display_name = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    original_filename = db.Column(db.String(255), nullable=True)
    glb_path = db.Column(db.String(500), nullable=False)
    qr_code_path = db.Column(db.String(500), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    source_format = db.Column(db.String(10), nullable=False, default="stl")
    processing_status = db.Column(db.String(30), nullable=False, default="ready")
    processing_error = db.Column(db.Text, nullable=True)
    anonymization_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    rights_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    ethics_responsibility_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    consent_confirmed_at = db.Column(db.DateTime, nullable=True)
    consent_ip = db.Column(db.String(100), nullable=True)
    terms_version = db.Column(db.String(20), nullable=False, default="1.0")
    created_at = db.Column(db.DateTime, default=utc_now)

    def __repr__(self) -> str:
        return f"<Model3D {self.id}>"


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    paper_id = db.Column(db.Integer, db.ForeignKey("papers.id"), nullable=True, index=True)
    amount_kurus = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="TRY")
    provider = db.Column(db.String(50), nullable=False, default="manual")
    provider_reference = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="pending")
    invoice_number = db.Column(db.String(80), nullable=True, unique=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    paid_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", backref=db.backref("payments", lazy=True))
    paper = db.relationship("Paper", backref=db.backref("payments", lazy=True))

    def __repr__(self) -> str:
        return f"<Payment {self.status} {self.amount_kurus} {self.currency}>"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    resource_id = db.Column(db.String(255), nullable=True, index=True)
    details = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    timestamp = db.Column(db.DateTime, default=utc_now, index=True)

    def __repr__(self) -> str:
        return f"<AuditLog {self.event_type} @ {self.timestamp}>"
