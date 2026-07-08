"""
SQLAlchemy database models: User, Paper, Model3D.
"""
from datetime import UTC, datetime
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
    # Set when an admin deactivates the account. A deactivated user cannot log in
    # and existing sessions are invalidated (see load_user). NULL means active.
    deactivated_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    papers = db.relationship(
        "Paper",
        backref="author",
        lazy=True,
        cascade="all, delete-orphan",
        foreign_keys="Paper.user_id",
    )

    @property
    def is_active(self) -> bool:
        """Flask-Login gate: deactivated accounts are treated as inactive so
        they cannot authenticate. Overrides UserMixin's always-True default."""
        return self.deactivated_at is None

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
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    package_type = db.Column(db.String(30), nullable=False, default="temporary")
    status = db.Column(db.String(30), nullable=False, default="active")
    is_public = db.Column(db.Boolean, nullable=False, default=False)
    payment_status = db.Column(db.String(30), nullable=False, default="free")
    payment_provider = db.Column(db.String(50), nullable=True)
    payment_reference = db.Column(db.String(200), nullable=True)
    pmid = db.Column(db.String(100), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True, default=None)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deleted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    deleted_by = db.relationship("User", foreign_keys=[deleted_by_user_id])

    models = db.relationship(
        "Model3D",
        backref="paper",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="Model3D.created_at",
    )

    def __repr__(self) -> str:
        return f"<Paper {self.title[:40]}>"


class Model3D(db.Model):
    __tablename__ = "models"

    id = db.Column(db.String(36), primary_key=True)  # UUID
    paper_id = db.Column(db.Integer, db.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    display_name = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    original_filename = db.Column(db.String(255), nullable=True)
    original_source_path = db.Column(db.String(500), nullable=True)
    current_source_path = db.Column(db.String(500), nullable=True)
    glb_path = db.Column(db.String(500), nullable=False)
    storage_provider = db.Column(db.String(40), nullable=False, default="railway_volume")
    storage_key = db.Column(db.String(500), nullable=True)
    qr_code_path = db.Column(db.String(500), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    # Precomputed bounding-box dimensions (e.g. "12.3 x 4.5 x 6.7 cm"), measured
    # once during conversion so listing/detail pages never have to re-parse the
    # GLB with trimesh on every request.
    dimensions_cm = db.Column(db.String(50), nullable=True)
    poster_path = db.Column(db.String(500), nullable=True)
    public_id = db.Column(db.String(40), unique=True, nullable=True, index=True)
    license_type = db.Column(db.String(30), nullable=False, default="free", index=True)
    license_status = db.Column(db.String(30), nullable=False, default="active", index=True)
    access_starts_at = db.Column(db.DateTime, nullable=True, default=utc_now)
    access_expires_at = db.Column(db.DateTime, nullable=True)
    storage_limit_bytes = db.Column(db.Integer, nullable=True)
    # Set when an institution's contract funded this model's "institutional"
    # license at upload time. Kept (SET NULL only on institution delete) even if
    # the member later leaves, so the model keeps counting against the
    # institution's quota — the institution paid for the storage.
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True)
    institution = db.relationship("Institution", backref=db.backref("funded_models", lazy=True))
    appearance_color = db.Column(db.String(20), nullable=True)
    appearance_roughness = db.Column(db.Float, nullable=True, default=0.35)
    appearance_metallic = db.Column(db.Float, nullable=True, default=0.05)
    ar_placement = db.Column(db.String(10), nullable=True, default="floor")
    replaced_at = db.Column(db.DateTime, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    replacement_status = db.Column(db.String(30), nullable=True)
    replacement_error = db.Column(db.Text, nullable=True)
    source_format = db.Column(db.String(10), nullable=False, default="stl")
    # Declared source unit for unitless formats (mm/cm/m), or "embedded" for
    # FBX/GLB. Stored so the edit page can re-interpret the unit (rescale by ratio).
    source_unit = db.Column(db.String(12), nullable=True)
    processing_status = db.Column(db.String(30), nullable=False, default="ready")
    processing_error = db.Column(db.Text, nullable=True)
    anonymization_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    rights_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    ethics_responsibility_confirmed = db.Column(db.Boolean, nullable=False, default=False)
    consent_confirmed_at = db.Column(db.DateTime, nullable=True)
    consent_ip = db.Column(db.String(100), nullable=True)
    terms_version = db.Column(db.String(20), nullable=False, default="1.0")
    # Set when the worker's synchronous R2 mirror of this model's converted
    # files fails (e.g. bad credentials, bucket unreachable); cleared on the
    # next successful mirror. NULL means "no known failure" — the async
    # (web-request) mirror path does not set this column (see services/r2_mirror.py).
    r2_mirror_failed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    def __repr__(self) -> str:
        return f"<Model3D {self.id}>"


class ModelAnnotation(db.Model):
    __tablename__ = "model_annotations"

    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.String(36), db.ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True)
    position_x = db.Column(db.Float, nullable=False)
    position_y = db.Column(db.Float, nullable=False)
    position_z = db.Column(db.Float, nullable=False)
    normal_x = db.Column(db.Float, nullable=False, default=0.0)
    normal_y = db.Column(db.Float, nullable=False, default=1.0)
    normal_z = db.Column(db.Float, nullable=False, default=0.0)
    label = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    order_index = db.Column(db.Integer, nullable=False, default=0)
    # Optional camera angle the owner was viewing from when the note was placed, so a
    # click can fly the camera back to it. model-viewer camera strings (orbit/target)
    # and field-of-view; null for annotations created before this feature.
    camera_orbit = db.Column(db.String(64), nullable=True)
    camera_target = db.Column(db.String(96), nullable=True)
    camera_fov = db.Column(db.String(16), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    model = db.relationship("Model3D", backref=db.backref("annotations", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self) -> str:
        return f"<ModelAnnotation {self.id} '{self.label}'>"

    def to_dict(self):
        return {
            "id": self.id,
            "model_id": self.model_id,
            "position": [self.position_x, self.position_y, self.position_z],
            "normal": [self.normal_x, self.normal_y, self.normal_z],
            "label": self.label,
            "description": self.description,
            "order_index": self.order_index,
            "camera": {
                "orbit": self.camera_orbit,
                "target": self.camera_target,
                "fov": self.camera_fov,
            } if self.camera_orbit else None,
        }


class QRLink(db.Model):
    __tablename__ = "qr_links"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(40), unique=True, nullable=False, index=True)
    model_id = db.Column(db.String(36), db.ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="active", index=True)
    target_type = db.Column(db.String(30), nullable=False, default="model_viewer")
    created_at = db.Column(db.DateTime, default=utc_now)
    last_resolved_at = db.Column(db.DateTime, nullable=True)

    model = db.relationship("Model3D", backref=db.backref("qr_links", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self) -> str:
        return f"<QRLink {self.public_id} -> {self.model_id}>"


class ModelVersion(db.Model):
    __tablename__ = "model_versions"

    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.String(36), db.ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True)
    version_number = db.Column(db.Integer, nullable=False)
    source_path = db.Column(db.String(500), nullable=True)
    glb_path = db.Column(db.String(500), nullable=True)
    source_format = db.Column(db.String(10), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    material_color = db.Column(db.String(20), nullable=True)
    storage_provider = db.Column(db.String(40), nullable=False, default="railway_volume")
    storage_key = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="queued", index=True)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    model = db.relationship("Model3D", backref=db.backref("versions", lazy=True, cascade="all, delete-orphan"))

    def __repr__(self) -> str:
        return f"<ModelVersion {self.model_id} v{self.version_number} {self.status}>"


class ConversionJob(db.Model):
    __tablename__ = "conversion_jobs"

    id = db.Column(db.Integer, primary_key=True)
    job_type = db.Column(db.String(40), nullable=False, default="model_upload", index=True)
    status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    model_id = db.Column(db.String(36), db.ForeignKey("models.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    payload = db.Column(db.JSON, nullable=False, default=dict)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=3)
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    model = db.relationship("Model3D", backref=db.backref("conversion_jobs", lazy=True, cascade="all, delete-orphan"))
    user = db.relationship("User", backref=db.backref("conversion_jobs", lazy=True))

    def __repr__(self) -> str:
        return f"<ConversionJob {self.job_type} {self.status} model={self.model_id}>"


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    paper_id = db.Column(db.Integer, db.ForeignKey("papers.id", ondelete="SET NULL"), nullable=True, index=True)
    # A payment buys a license window for a single Model3D. Kept nullable +
    # ondelete=SET NULL so the payment/invoice record survives if the model is
    # later deleted (financial audit trail must outlive the asset).
    model_id = db.Column(db.String(36), db.ForeignKey("models.id", ondelete="SET NULL"), nullable=True, index=True)
    # B2B: an institution's offline contract payment (invoice/bank transfer),
    # recorded manually by the platform admin. Same survival rationale as
    # model_id — the financial record must outlive the institution row.
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id", ondelete="SET NULL"), nullable=True, index=True)
    # The license plan this payment grants (academic / extended_archive). Needed
    # because some gateways (e.g. PayTR) don't echo custom data in the callback,
    # so the plan is recovered from this row by merchant_oid (provider_reference).
    plan_key = db.Column(db.String(30), nullable=True)
    # Amount in the smallest currency unit (kurus for TRY, cents for USD/EUR).
    # The column name is historical; treat it as generic minor units.
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
    model = db.relationship("Model3D", backref=db.backref("payments", lazy=True))
    institution = db.relationship("Institution", backref=db.backref("payments", lazy=True))

    def __repr__(self) -> str:
        return f"<Payment {self.status} {self.amount_kurus} {self.currency}>"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resource_id = db.Column(db.String(255), nullable=True, index=True)
    details = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    timestamp = db.Column(db.DateTime, default=utc_now, index=True)

    def __repr__(self) -> str:
        return f"<AuditLog {self.event_type} @ {self.timestamp}>"


class BlogPost(db.Model):
    """Admin-authored blog post (Markdown body), managed from the admin panel.

    The built-in launch articles are defined in ``blog_content.py`` and seeded
    into this table on startup (``app.seed_builtin_blog_posts``, self-healing
    like ``seed_license_plans``) so they show up and are editable in the admin
    panel too. The public blog merges DB rows (which take precedence by slug)
    with any not-yet-seeded code posts.
    """
    __tablename__ = "blog_posts"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(250), unique=True, nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    body = db.Column(db.Text, nullable=False)  # Markdown source
    tags = db.Column(db.String(300), nullable=True)  # comma-separated
    persona = db.Column(db.String(120), nullable=True)
    author = db.Column(db.String(120), nullable=True, default="AcademicAR Team")
    read_minutes = db.Column(db.Integer, nullable=True)
    is_published = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime, default=utc_now, index=True)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    def __repr__(self) -> str:
        return f"<BlogPost {self.slug}>"


class LicensePlanConfig(db.Model):
    """Admin-editable price/duration/storage facts for a license plan key.

    Rows are seeded at startup (app.seed_license_plans) from licensing.py's
    Python-side defaults, mirroring sync_configured_admins. Read through
    licensing.get_license_plan()/get_license_plans(), which cache-aside this
    table — application code should not query this model directly except in
    the admin pricing routes and the seed/cache-refresh functions.
    """
    __tablename__ = "license_plans"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(30), unique=True, nullable=False, index=True)
    label = db.Column(db.String(80), nullable=False)
    # Minor units (cents) — matches Payment.amount_kurus's convention; avoids
    # float rounding drift across repeated admin edits.
    price_usd_cents = db.Column(db.Integer, nullable=False, default=0)
    duration_days = db.Column(db.Integer, nullable=True)  # NULL = unlimited
    storage_limit_bytes = db.Column(db.Integer, nullable=False)
    is_purchasable = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    def __repr__(self) -> str:
        return f"<LicensePlanConfig {self.key}>"


class Institution(db.Model):
    """A university/lab/institute on a B2B contract.

    Created and priced manually by the platform admin (offline invoicing —
    there is no self-serve institutional checkout). Members join via invite
    links; their uploads get the "institutional" license plan while the
    contract is current and the quota below is not exhausted (see
    institutions.py helpers).
    """
    __tablename__ = "institutions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False, index=True)
    # Comma-separated lowercase email domains without "@" (e.g.
    # "boun.edu.tr, metu.edu.tr"). When set, invite acceptance requires the
    # joining user's email to match one of them; empty = any email may join.
    email_domains = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)  # active | suspended
    contract_starts_at = db.Column(db.DateTime, nullable=True)
    contract_ends_at = db.Column(db.DateTime, nullable=True)  # NULL = open-ended
    # Contract price in minor units (kurus for TRY, cents for USD) — same
    # convention as Payment.amount_kurus. Informational; actual money is
    # tracked via Payment rows with institution_id set.
    annual_price_cents = db.Column(db.Integer, nullable=True)
    currency = db.Column(db.String(3), nullable=False, default="TRY")
    # Contract quota across ALL models the institution has funded. NULL means
    # unlimited. BigInteger: storage contracts exceed the 2 GB int32 range.
    quota_storage_bytes = db.Column(db.BigInteger, nullable=True)
    quota_model_count = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)

    def domain_list(self) -> list[str]:
        if not self.email_domains:
            return []
        return [d.strip().lower().lstrip("@") for d in self.email_domains.split(",") if d.strip()]

    def email_matches_domains(self, email: str | None) -> bool:
        """True when the email's domain is allowed. No domains configured = allow any."""
        domains = self.domain_list()
        if not domains:
            return True
        domain = (email or "").rsplit("@", 1)[-1].strip().lower()
        return bool(domain) and domain in domains

    def contract_is_current(self) -> bool:
        """True when today falls inside the contract window (NULL bounds are
        open). Mirrors licensing.is_access_expired's naive-UTC convention."""
        now = datetime.now(UTC)
        starts = self.contract_starts_at
        if starts is not None:
            if starts.tzinfo is None:
                starts = starts.replace(tzinfo=UTC)
            if starts > now:
                return False
        ends = self.contract_ends_at
        if ends is not None:
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=UTC)
            if ends < now:
                return False
        return True

    def __repr__(self) -> str:
        return f"<Institution {self.name}>"


class InstitutionInvite(db.Model):
    """A shareable join link for an institution, created from the institution
    panel. Stored as a row (not a signed token) because invites need
    revocation, max-use counting, and listing."""
    __tablename__ = "institution_invites"

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)  # NULL = no expiry
    max_uses = db.Column(db.Integer, nullable=True)  # NULL = unlimited
    use_count = db.Column(db.Integer, nullable=False, default=0)
    revoked_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now)

    institution = db.relationship(
        "Institution",
        backref=db.backref("invites", lazy=True, cascade="all, delete-orphan"),
    )
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self) -> str:
        return f"<InstitutionInvite {self.id} inst={self.institution_id}>"


class InstitutionMember(db.Model):
    """Membership of a user in an institution.

    v1: one institution per user (UNIQUE on user_id) so exactly one
    institution can fund an upload; relaxing to multi-institution later is
    just dropping that constraint. role is "member" or "admin" — institution
    admins manage the /institution panel and are unrelated to User.is_admin.
    """
    __tablename__ = "institution_members"

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    role = db.Column(db.String(20), nullable=False, default="member")  # member | admin
    joined_at = db.Column(db.DateTime, default=utc_now)
    invite_id = db.Column(db.Integer, db.ForeignKey("institution_invites.id", ondelete="SET NULL"), nullable=True)

    institution = db.relationship(
        "Institution",
        backref=db.backref("members", lazy=True, cascade="all, delete-orphan"),
    )
    user = db.relationship("User", backref=db.backref("institution_membership", uselist=False))
    invite = db.relationship("InstitutionInvite", foreign_keys=[invite_id])

    def __repr__(self) -> str:
        return f"<InstitutionMember user={self.user_id} inst={self.institution_id} {self.role}>"

