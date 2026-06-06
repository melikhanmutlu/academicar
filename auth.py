"""Authentication blueprint: email/password login, registration, and Google OAuth."""
import os
from urllib.parse import urljoin, urlparse

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_limiter.util import get_remote_address
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError

from extensions import limiter
from models import User, db
from url_helpers import public_url

# Single source of truth — also exported via Config.PASSWORD_MIN_LENGTH so the
# frontend, account routes, and registration form agree on the same minimum.
PASSWORD_MIN_LENGTH = int(os.environ.get("PASSWORD_MIN_LENGTH", 8))

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")
try:
    from authlib.integrations.flask_client import OAuth
except ImportError:
    OAuth = None

oauth = OAuth() if OAuth else None


def _auth_ip_email_key() -> str:
    """Rate-limit bucket keyed by client IP + submitted email so brute-force
    against one account (or from one IP) is throttled without locking everyone."""
    email = (request.form.get("email") or "").lower().strip()
    return f"auth:{get_remote_address()}:{email}"


def _login_failed(response) -> bool:
    """Count an attempt against the lockout limit only when login did NOT
    succeed. A successful login redirects (302); failures re-render (200)."""
    return getattr(response, "status_code", 200) != 302


def is_safe_redirect_url(target: str) -> bool:
    """Allow redirects only within the current site."""
    if not target:
        return False
    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    return redirect_url.scheme in {"http", "https"} and host_url.netloc == redirect_url.netloc


def init_oauth(app):
    """Initialize OAuth client with app config."""
    if oauth is None:
        return
    oauth.init_app(app)
    if app.config.get("GOOGLE_CLIENT_ID"):
        oauth.register(
            name="google",
            client_id=app.config["GOOGLE_CLIENT_ID"],
            client_secret=app.config["GOOGLE_CLIENT_SECRET"],
            server_metadata_url=app.config["GOOGLE_DISCOVERY_URL"],
            client_kwargs={"scope": "openid email profile"},
        )


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember me")
    submit = SubmitField("Log in")


class RegistrationForm(FlaskForm):
    username = StringField("Full name", validators=[DataRequired(), Length(min=2, max=80)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField(
        "Password",
        validators=[
            DataRequired(),
            Length(
                min=PASSWORD_MIN_LENGTH,
                max=1024,
                message=f"Password must be between {PASSWORD_MIN_LENGTH} and 1024 characters.",
            ),
        ],
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords do not match.")],
    )
    submit = SubmitField("Sign up")

    def validate_email(self, field):
        if User.query.filter_by(email=field.data.lower()).first():
            raise ValidationError("This email is already registered.")


class ForgotPasswordForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Send reset link")


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        "New password",
        validators=[
            DataRequired(),
            Length(
                min=PASSWORD_MIN_LENGTH,
                max=1024,
                message=f"Password must be between {PASSWORD_MIN_LENGTH} and 1024 characters.",
            ),
        ],
    )
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords do not match.")],
    )
    submit = SubmitField("Reset password")


def _password_reset_serializer():
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")


def generate_password_reset_token(user) -> str:
    """Signed, time-limited token carrying the user id (exported for tests)."""
    return _password_reset_serializer().dumps({"uid": user.id})


def verify_password_reset_token(token: str, max_age: int = 3600):
    """Return the user id for a valid token, or None if invalid/expired."""
    from itsdangerous import BadSignature, SignatureExpired

    try:
        data = _password_reset_serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("uid")


def _rotate_session():
    """Drop any pre-login session contents to defeat session fixation.

    Called right before login_user() / logout_user() so an attacker who knows
    a victim's pre-auth session id cannot ride it into an authenticated state.
    """
    session.clear()


def _apply_configured_admin(user: User) -> None:
    admin_emails = current_app.config.get("ADMIN_EMAILS", [])
    if isinstance(admin_emails, str):
        admin_emails = admin_emails.split(",")
    normalized = {str(email).strip().lower() for email in admin_emails if str(email).strip()}
    if user.email.strip().lower() in normalized:
        user.is_admin = True


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"], key_func=get_remote_address)
@limiter.limit("3 per minute", methods=["POST"], key_func=get_remote_address)
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    form = RegistrationForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip(),
            email=form.email.data.lower().strip(),
        )
        user.set_password(form.password.data)
        _apply_configured_admin(user)
        db.session.add(user)
        db.session.commit()
        _rotate_session()
        login_user(user)
        # Log registration for privacy compliance
        try:
            from app import log_audit
            log_audit("user_registered", user_id=user.id)
        except Exception:
            pass  # Fail silently if audit logging fails
        flash("Registration successful. Welcome.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "register.html",
        form=form,
        google_oauth_enabled=bool(current_app.config.get("GOOGLE_CLIENT_ID") and current_app.config.get("GOOGLE_CLIENT_SECRET")),
    )


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("30 per hour", methods=["POST"], key_func=get_remote_address)
@limiter.limit(
    "5 per minute",
    methods=["POST"],
    key_func=_auth_ip_email_key,
    deduct_when=_login_failed,
)
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(form.password.data):
            if not user.is_admin:
                _apply_configured_admin(user)
                if user.is_admin:
                    db.session.commit()
            _rotate_session()
            login_user(user, remember=form.remember.data)
            try:
                from app import log_audit
                log_audit("user_login", user_id=user.id, details={"provider": "password"})
            except Exception:
                pass
            next_page = request.args.get("next")
            if next_page and is_safe_redirect_url(next_page):
                return redirect(next_page)
            return redirect(url_for("dashboard"))
        # Failed-login audit (no email enumeration: same flash, audit logs the
        # attempted email so legitimate password-reset abuse can be tracked).
        try:
            from app import log_audit
            log_audit("user_login_failed", user_id=user.id if user else None, details={"email": email})
        except Exception:
            pass
        flash("Invalid email or password.", "danger")

    return render_template(
        "login.html",
        form=form,
        google_oauth_enabled=bool(current_app.config.get("GOOGLE_CLIENT_ID") and current_app.config.get("GOOGLE_CLIENT_SECRET")),
    )


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"], key_func=get_remote_address)
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        user = User.query.filter_by(email=email).first()
        # Only password accounts can reset a password (Google-only accounts have
        # no password_hash). We never reveal which case applies — see below.
        if user and user.password_hash:
            token = generate_password_reset_token(user)
            reset_url = public_url("auth.reset_password", token=token)
            from utils.email import send_email

            send_email(
                email,
                "Reset your AcademicAR password",
                (
                    "We received a request to reset your AcademicAR password.\n\n"
                    f"To choose a new password, open this link within 1 hour:\n{reset_url}\n\n"
                    "If you did not request this, you can ignore this email; your "
                    "password stays unchanged."
                ),
            )
            try:
                from app import log_audit

                log_audit("password_reset_requested", user_id=user.id, details={"email": email})
            except Exception:
                pass
        # Identical response whether or not the account exists, so the form can't
        # be used to enumerate registered emails.
        flash("If an account exists for that email, we've sent a password reset link.", "info")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html", form=form)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"], key_func=get_remote_address)
def reset_password(token):
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    uid = verify_password_reset_token(token)
    if uid is None:
        flash("This password reset link is invalid or has expired. Please request a new one.", "warning")
        return redirect(url_for("auth.forgot_password"))
    user = db.session.get(User, uid)
    if user is None:
        abort(404)

    form = ResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        try:
            from app import log_audit

            log_audit("password_reset_completed", user_id=user.id)
        except Exception:
            pass
        flash("Your password has been reset. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", form=form, token=token)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out.", "info")
    return redirect(url_for("landing"))


@auth_bp.route("/google")
def google_login():
    if oauth is None or not current_app.config.get("GOOGLE_CLIENT_ID"):
        flash("Google login is not configured yet.", "warning")
        return redirect(url_for("auth.login"))
    redirect_uri = public_url("auth.google_callback")
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback")
@limiter.limit("20 per hour", key_func=get_remote_address)
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        flash(f"Google login failed: {e}", "danger")
        return redirect(url_for("auth.login"))

    userinfo = token.get("userinfo")
    if not userinfo:
        userinfo = oauth.google.userinfo()

    google_id = userinfo.get("sub")
    email = (userinfo.get("email") or "").lower().strip()
    name = userinfo.get("name") or email.split("@")[0]
    picture = userinfo.get("picture")

    if not google_id or not email:
        flash("Could not retrieve user information from Google.", "danger")
        return redirect(url_for("auth.login"))
    if userinfo.get("email_verified") is not True:
        flash("Your Google account email address is not verified.", "danger")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(google_id=google_id).first()
    is_new_user = False
    if not user:
        existing = User.query.filter_by(email=email).first()
        if existing:
            # Auto-link the Google identity to the matching local account.
            # This is safe because Google already asserted email_verified=True
            # (checked above), meaning Google has confirmed this person owns
            # the address — so linking is equivalent to a verified identity
            # merge, not an account takeover.
            existing.google_id = google_id
            if not existing.avatar_url and picture:
                existing.avatar_url = picture
            _apply_configured_admin(existing)
            user = existing
            # Let the user know their accounts were merged on first Google login
            if existing.password_hash:
                flash(
                    "Your Google account has been linked to your existing account. "
                    "You can now sign in with either Google or your password.",
                    "info",
                )
        else:
            user = User(
                email=email,
                username=name,
                google_id=google_id,
                avatar_url=picture,
            )
            _apply_configured_admin(user)
            db.session.add(user)
            is_new_user = True
    else:
        _apply_configured_admin(user)
    db.session.commit()

    _rotate_session()
    login_user(user)

    # Log registration/login for privacy compliance
    try:
        from app import log_audit
        if is_new_user:
            log_audit("user_registered", user_id=user.id, details={"provider": "google"})
        else:
            log_audit("user_login", user_id=user.id, details={"provider": "google"})
    except Exception:
        pass  # Fail silently if audit logging fails

    flash(f"Welcome, {user.username}.", "success")
    return redirect(url_for("dashboard"))
