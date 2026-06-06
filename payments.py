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

import base64
import hashlib
import hmac
import logging
import uuid
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
    # Plain-text body a provider requires in the webhook acknowledgement (e.g.
    # PayTR expects literally "OK"). None -> the route replies with JSON.
    webhook_ack: str | None = None

    def create_checkout(self, *, payment, model, plan_key, user, success_url, cancel_url):
        """Return a URL to redirect the buyer to, or ``None`` if unavailable."""
        raise NotImplementedError

    def verify_webhook(self, request) -> bool:
        """Return True if the inbound webhook is authentic."""
        return False

    def parse_event(self, request) -> dict | None:
        """Normalize a webhook into a dict with keys: provider_reference,
        status ('paid'/...), plan_key, model_id, payment_id. Missing plan_key /
        model_id are resolved from the stored Payment row by the route."""
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


class PayTRProvider(PaymentProvider):
    """PayTR (Turkish payment gateway) integration.

    create_checkout requests an iFrame token from PayTR and returns the hosted
    payment URL. PayTR's server-to-server callback (configured as the
    "Bildirim URL" in the PayTR panel, pointing at /payment/webhook/paytr) is a
    form POST whose ``hash`` we verify; we must reply with the literal text
    ``OK``. The callback does NOT echo custom data, so the plan and model are
    recovered from the stored Payment row via ``merchant_oid`` (== provider_reference).

    NOTE: PayTR is a payment gateway, not a Merchant of Record — global VAT/tax
    and invoicing remain the seller's responsibility (see
    docs/MVP_ANALYSIS_AND_ROADMAP.md). Requires PAYTR_MERCHANT_ID/KEY/SALT.
    """

    name = "paytr"
    webhook_ack = "OK"

    def _credentials(self):
        cfg = current_app.config
        return (
            cfg.get("PAYTR_MERCHANT_ID"),
            cfg.get("PAYTR_MERCHANT_KEY"),
            cfg.get("PAYTR_MERCHANT_SALT"),
        )

    def create_checkout(self, *, payment, model, plan_key, user, success_url, cancel_url):
        import json

        from flask import request
        import requests

        merchant_id, merchant_key, merchant_salt = self._credentials()
        if not (merchant_id and merchant_key and merchant_salt):
            logger.warning("PayTR credentials are not configured; set PAYTR_MERCHANT_* env vars.")
            return None

        merchant_oid = f"AAR{payment.id}{uuid.uuid4().hex[:8]}"
        payment.provider_reference = merchant_oid
        amount = max(int(payment.amount_kurus), 1)  # already in minor units
        forwarded = request.headers.get("X-Forwarded-For", "")
        user_ip = (forwarded.split(",")[0].strip() if forwarded else None) or request.remote_addr or "127.0.0.1"
        currency = (current_app.config.get("PAYMENT_CURRENCY") or "USD").upper()
        paytr_currency = "TL" if currency in {"TRY", "TL"} else currency
        test_mode = "1" if current_app.config.get("PAYTR_TEST_MODE") else "0"
        no_installment, max_installment = "1", "0"
        basket = base64.b64encode(
            json.dumps([[get_license_plan(plan_key).label, f"{amount / 100:.2f}", 1]]).encode()
        ).decode()

        hash_str = (
            f"{merchant_id}{user_ip}{merchant_oid}{user.email}{amount}{basket}"
            f"{no_installment}{max_installment}{paytr_currency}{test_mode}"
        )
        paytr_token = base64.b64encode(
            hmac.new(merchant_key.encode(), (hash_str + merchant_salt).encode(), hashlib.sha256).digest()
        ).decode()

        params = {
            "merchant_id": merchant_id,
            "user_ip": user_ip,
            "merchant_oid": merchant_oid,
            "email": user.email,
            "payment_amount": amount,
            "paytr_token": paytr_token,
            "user_basket": basket,
            "debug_on": test_mode,
            "no_installment": no_installment,
            "max_installment": max_installment,
            "user_name": (user.username or user.email)[:60],
            "user_address": "N/A",
            "user_phone": "0000000000",
            "merchant_ok_url": success_url,
            "merchant_fail_url": cancel_url,
            "timeout_limit": "30",
            "currency": paytr_currency,
            "test_mode": test_mode,
            "lang": "en",
        }
        try:
            resp = requests.post("https://www.paytr.com/odeme/api/get-token", data=params, timeout=20)
            data = resp.json()
        except Exception as exc:  # pragma: no cover - network dependent
            logger.error("PayTR get-token request failed: %s", exc)
            return None
        if data.get("status") == "success":
            return f"https://www.paytr.com/odeme/guvenli/{data['token']}"
        logger.error("PayTR get-token error: %s", data.get("reason"))
        return None

    def verify_webhook(self, request) -> bool:
        _, merchant_key, merchant_salt = self._credentials()
        if not (merchant_key and merchant_salt):
            return False
        merchant_oid = request.form.get("merchant_oid", "")
        status = request.form.get("status", "")
        total_amount = request.form.get("total_amount", "")
        provided = request.form.get("hash", "")
        token = base64.b64encode(
            hmac.new(
                merchant_key.encode(),
                (merchant_oid + merchant_salt + status + total_amount).encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        return bool(provided) and hmac.compare_digest(token, provided)

    def parse_event(self, request) -> dict | None:
        status = request.form.get("status", "")
        return {
            "provider_reference": request.form.get("merchant_oid") or None,
            "status": "paid" if status == "success" else (status or "pending"),
            "plan_key": None,  # recovered from the stored Payment.plan_key
            "model_id": None,  # recovered from the stored Payment.model_id
            "payment_id": None,
        }


_PROVIDERS: dict[str, PaymentProvider] = {
    p.name: p for p in (DevProvider(), LemonSqueezyProvider(), PayTRProvider())
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
