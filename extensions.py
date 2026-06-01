"""Shared Flask extension singletons.

Defined in their own module so blueprints (e.g. ``auth``) can attach rate
limits without importing ``app`` and creating a circular import.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user
from flask_wtf.csrf import CSRFProtect


def rate_limit_key() -> str:
    """Rate-limit bucket key: authenticated user id, else client IP."""
    if current_user.is_authenticated:
        return f"user:{current_user.id}"
    return f"ip:{get_remote_address()}"


csrf = CSRFProtect()
limiter = Limiter(key_func=rate_limit_key, default_limits=[])
