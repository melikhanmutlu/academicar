"""URL helpers for externally shared links."""
from urllib.parse import urljoin

from flask import current_app, url_for, has_request_context


def public_url(endpoint: str, **values) -> str:
    """Build public links from SITE_URL instead of trusting the request host."""
    if not has_request_context():
        with current_app.test_request_context():
            return public_url(endpoint, **values)

    site_url = (current_app.config.get("SITE_URL") or "").strip()
    if not site_url:
        return url_for(endpoint, _external=True, **values)
    path = url_for(endpoint, **values)
    return urljoin(site_url.rstrip("/") + "/", path.lstrip("/"))
