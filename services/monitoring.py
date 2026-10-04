"""Error monitoring (Sentry). Off unless SENTRY_DSN is set.

Initialised once per process from create_app(), so both the web process and
the worker (which also calls create_app) report unhandled exceptions and
logger.exception(...) calls. No user PII is sent.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_initialised = False


def init_error_monitoring(app_env: str) -> bool:
    """Start Sentry when SENTRY_DSN is configured. Returns True if enabled."""
    global _initialised
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn or _initialised:
        return _initialised
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; error monitoring is off.")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=app_env,
        release=os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None,
        integrations=[FlaskIntegration()],
        # Performance tracing is opt-in (it counts against the Sentry quota).
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or 0),
        send_default_pii=False,
    )
    _initialised = True
    logger.info("Sentry error monitoring enabled (%s).", app_env)
    return True
