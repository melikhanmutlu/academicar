"""Sign-up bot protection.

Every sign-up sends a welcome/confirmation email, so a bot that registers
strangers' addresses turns the site into a mail cannon. Checks, cheapest first:

1. Honeypot: a hidden ``website`` field people never fill in.
2. Form timing: a signed timestamp issued when the form renders; posts that
   come back faster than ``SIGNUP_MIN_SECONDS`` (or without a valid stamp) fail.
3. Cloudflare Turnstile, when ``TURNSTILE_SITE_KEY``/``TURNSTILE_SECRET_KEY``
   are set.

``SIGNUP_BOT_CHECKS=0`` switches all of them off (tests, emergencies).
"""
import json
import time
import urllib.parse
import urllib.request

from flask import current_app
from itsdangerous import BadSignature, URLSafeTimedSerializer

HONEYPOT_FIELD = "website"
TIMING_FIELD = "form_started"
TURNSTILE_RESPONSE_FIELD = "cf-turnstile-response"
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
# A stamp older than this is treated as missing (the CSRF token expires first anyway).
TIMING_MAX_AGE = 24 * 3600


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="signup-form")


def checks_enabled() -> bool:
    return bool(current_app.config.get("SIGNUP_BOT_CHECKS", True))


def turnstile_site_key() -> str:
    """Site key to render the widget with, or "" when Turnstile is off."""
    cfg = current_app.config
    if checks_enabled() and cfg.get("TURNSTILE_SITE_KEY") and cfg.get("TURNSTILE_SECRET_KEY"):
        return cfg["TURNSTILE_SITE_KEY"]
    return ""


def issue_form_stamp() -> str:
    return _serializer().dumps({"t": time.time()})


def _verify_turnstile(token: str, remote_ip: str | None) -> bool:
    data = {"secret": current_app.config["TURNSTILE_SECRET_KEY"], "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    req = urllib.request.Request(
        TURNSTILE_VERIFY_URL,
        data=urllib.parse.urlencode(data).encode(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return bool(json.loads(response.read().decode()).get("success"))
    except Exception:
        # Fail closed: an unverifiable token is not a passed challenge. The
        # honeypot/timing checks already ran, and the user can simply retry.
        current_app.logger.warning("turnstile verification request failed", exc_info=True)
        return False


def check_signup(form_data, remote_ip: str | None) -> str | None:
    """Return a short reason when the submission looks automated, else None."""
    if not checks_enabled():
        return None
    if (form_data.get(HONEYPOT_FIELD) or "").strip():
        return "honeypot"
    stamp = form_data.get(TIMING_FIELD) or ""
    try:
        started = float(_serializer().loads(stamp, max_age=TIMING_MAX_AGE)["t"])
    except (BadSignature, KeyError, TypeError, ValueError):
        return "missing_stamp"
    if time.time() - started < float(current_app.config.get("SIGNUP_MIN_SECONDS", 3)):
        return "too_fast"
    if turnstile_site_key():
        token = (form_data.get(TURNSTILE_RESPONSE_FIELD) or "").strip()
        if not token or not _verify_turnstile(token, remote_ip):
            return "turnstile"
    return None
