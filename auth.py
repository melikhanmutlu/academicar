"""Authentication blueprint: email/password login, registration, and Google OAuth."""
import os
from urllib.parse import urljoin, urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError

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
                message=f"Password must be at least {PASSWORD_MIN_LENGTH} characters.",
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


def _rotate_session():
    """Drop any pre-login session contents to defeat session fixation.

    Called right before login_user() / logout_user() so an attacker who knows
    a victim's pre-auth session id cannot ride it into an authenticated state.
    """
    session.clear()


@auth_bp.route("/register", methods=["GET", "POST"])
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
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(form.password.data):
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
            # Don't auto-link a Google identity into an existing local account
            # if it already has a password set (i.e. the email belongs to a
            # different signup path). This prevents takeover of squatted
            # accounts and forces the user to log in with their password first.
            if existing.password_hash:
                flash(
                    "This email is already registered with a password. "
                    "Please log in first, then link Google from your profile.",
                    "warning",
                )
                return redirect(url_for("auth.login"))
            existing.google_id = google_id
            if not existing.avatar_url and picture:
                existing.avatar_url = picture
            user = existing
        else:
            user = User(
                email=email,
                username=name,
                google_id=google_id,
                avatar_url=picture,
            )
            db.session.add(user)
            is_new_user = True
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
