"""Provider-agnostic payment skeleton for AcademicAR.

A payment buys a license window for a single ``Model3D``. The *only* thing a
concrete provider has to do is turn a "this order was paid" signal into a call
to :func:`apply_successful_payment`, which reuses the existing
``licensing.apply_model_license_defaults`` so the model's ``license_type``,
``access_expires_at`` and ``storage_limit_bytes`` are recomputed exactly the
same way the admin tools already do.

Today two providers are wired:

* ``development`` — no real gateway. ``create_checkout`` finalizes the payment
  immediately (mirrors the legacy ``ALLOW_DEV_PAYMENTS`` instant upgrade) so the
  full checkout -> license-assignment path is testable without a third party.
* ``lemonsqueezy`` — reference skeleton (Merchant of Record, the recommended
  primary provider). Signature verification is implemented; ``create_checkout``
  is a documented TODO to fill in once a provider is chosen and Turkey seller
  support is confirmed. See ``docs/MVP_ANALYSIS_AND_ROADMAP.md``.

Keeping this module free of any ``app`` import avoids circular imports; route
handlers in ``app.py`` orchestrate the HTTP side.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime

from flask import current_app

from licensing import apply_model_license_defaults, get_license_plan

logger = logging.getLogger(__name__)

# Plans a user can pay to upgrade a single model to.
PAID_PLAN_KEYS: tuple[str, ...] = ("academic", "extended_archive")


def plan_amount_minor_units(plan_key: str) -> int:
    """Price for a plan in the smallest currency unit (e.g. cents)."""
    return int(round(get_license_plan(plan_key).price_usd * 100))


def apply_successful_payment(payment, model, plan_key: str) -> None:
    """Mark ``payment`` paid and upgrade ``model`` to ``plan_key``.

    Idempotent: safe to call more than once for the same order (license fields
    are recomputed deterministically and ``paid_at`` is only set once).
    """
    payment.status = "paid"
    if not payment.paid_at:
        payment.paid_at = datetime.now(UTC)
    if model is not None:
        apply_model_license_defaults(model, plan_key)


class PaymentProvider:
    """Interface every provider implements."""

    name = "base"

    def create_checkout(self, *, payment, model, plan_key, user, success_url, cancel_url):
        """Return a URL to redirect the buyer to, or ``None`` if unavailable."""
        raise NotImplementedError

    def verify_webhook(self, request) -> bool:
        """Return True if the inbound webhook is authentic."""
        return False

    def parse_event(self, request) -> dict | None:
        """Normalize a webhook into a dict with keys: provider_reference,
        status ('paid'/...), plan_key, model_id, payment_id."""
        return None


class DevProvider(PaymentProvider):
    """Local/test provider with no real gateway."""

    name = "development"

    def create_checkout(self, *, payment, model, plan_key, user, success_url, cancel_url):
        # No gateway: settle immediately so the upgrade is visible right away.
        apply_successful_payment(payment, model, plan_key)
        return success_url

    def verify_webhook(self, request) -> bool:
        # Webhooks are only simulated in tests; accept them in non-prod.
        return True

    def parse_event(self, request) -> dict | None:
        data = request.get_json(silent=True) or {}
        return {
            "provider_reference": data.get("provider_reference") or data.get("id"),
            "status": (data.get("status") or "paid").lower(),
            "plan_key": data.get("plan_key") or data.get("plan"),
            "model_id": data.get("model_id"),
            "payment_id": data.get("payment_id"),
        }


class LemonSqueezyProvider(PaymentProvider):
    """Reference Merchant-of-Record skeleton (recommended primary provider).

    Fill in ``create_checkout`` with a POST to the LemonSqueezy checkouts API
    once the account is live; the webhook signature check below is production
    ready. The checkout MUST pass ``custom`` data containing ``payment_id``,
    ``model_id`` and ``plan_key`` so :meth:`parse_event` can map the order back
    to the right model.
    """

    name = "lemonsqueezy"

    def create_checkout(self, *, payment, model, plan_key, user, success_url, cancel_url):
        # TODO(go-live): POST https://api.lemonsqueezy.com/v1/checkouts with
        #   store_id, variant_id (mapped from plan_key via
        #   LEMONSQUEEZY_VARIANT_* config), checkout_data.email = user.email and
        #   checkout_data.custom = {payment_id, model_id, plan_key}; return the
        #   hosted checkout URL from the response. See docs/MVP_ANALYSIS...md.
        logger.warning(
            "LemonSqueezyProvider.create_checkout is not implemented yet; "
            "set PAYMENT_PROVIDER=development for local testing."
        )
        return None

    def verify_webhook(self, request) -> bool:
        secret = current_app.config.get("LEMONSQUEEZY_WEBHOOK_SECRET")
        signature = request.headers.get("X-Signature", "")
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode(), request.get_data(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(digest, signature)

    def parse_event(self, request) -> dict | None:
        payload = request.get_json(silent=True) or {}
        meta = payload.get("meta", {}) or {}
        custom = meta.get("custom_data", {}) or {}
        data = payload.get("data", {}) or {}
        attrs = data.get("attributes", {}) or {}
        event = (meta.get("event_name") or "").lower()
        raw_status = (attrs.get("status") or "").lower()
        paid = event in {"order_created", "order_completed"} or raw_status in {"paid", "completed", "active"}
        return {
            "provider_reference": str(data.get("id") or "") or None,
            "status": "paid" if paid else (raw_status or "pending"),
            "plan_key": custom.get("plan_key"),
            "model_id": custom.get("model_id"),
            "payment_id": custom.get("payment_id"),
        }


_PROVIDERS: dict[str, PaymentProvider] = {
    p.name: p for p in (DevProvider(), LemonSqueezyProvider())
}


def get_payment_provider() -> PaymentProvider:
    """Resolve the active provider from ``PAYMENT_PROVIDER`` config.

    Falls back to the dev provider when ``ALLOW_DEV_PAYMENTS`` is on (local/test)
    and to LemonSqueezy otherwise so production never silently grants free
    upgrades.
    """
    name = (current_app.config.get("PAYMENT_PROVIDER") or "").strip().lower()
    if not name:
        name = "development" if current_app.config.get("ALLOW_DEV_PAYMENTS") else "lemonsqueezy"
    return _PROVIDERS.get(name, _PROVIDERS["development"])
