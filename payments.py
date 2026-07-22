"""Provider-agnostic payment skeleton for AcademicAR.

A payment buys a license window for a single ``Model3D``. The *only* thing a
concrete provider has to do is turn a "this order was paid" signal into a call
to :func:`apply_successful_payment`, which reuses the existing
``licensing.apply_model_license_defaults`` so the model's ``license_type``,
``access_expires_at`` and ``storage_limit_bytes`` are recomputed exactly the
same way the admin tools already do.

Three providers are wired:

* ``development`` — no real gateway. ``create_checkout`` finalizes the payment
  immediately (mirrors the legacy ``ALLOW_DEV_PAYMENTS`` instant upgrade) so the
  full checkout -> license-assignment path is testable without a third party.
* ``paytr`` — live Turkish gateway (settles in TRY; USD list prices are
  converted via the TCMB daily rate). iFrame-token checkout plus a
  hash-verified, amount-checked callback that must be acknowledged with ``OK``.
* ``lemonsqueezy`` — Merchant of Record for international (USD) sales. Both the
  hosted checkout (``create_checkout``, gated on LEMONSQUEEZY_* config so it is
  inert until credentials are set) and the signature-verified webhook are
  implemented. See ``docs/MVP_ANALYSIS_AND_ROADMAP.md``.

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

_TCMB_TODAY_XML_URL = "https://www.tcmb.gov.tr/kurlar/today.xml"
_FX_CACHE_TTL_SECONDS = 3600  # TCMB republishes once/business day; hourly refetch is plenty.
_fx_cache: dict[str, object] = {"rate": None, "fetched_at": None}


class ForexRateUnavailable(Exception):
    """Raised when the USD/TRY rate cannot be determined and no previously
    fetched (even stale) rate is cached to fall back on."""


def get_usd_try_forex_selling_rate() -> float:
    """USD/TRY "döviz satış" (foreign-exchange selling) rate from the Turkish
    Central Bank (TCMB), used to convert USD-priced plans into TRY for
    gateways (PayTR) that settle in TRY.

    A transient TCMB outage must not block checkout, so a failed refetch
    falls back to the last known-good rate; :class:`ForexRateUnavailable` is
    raised only when no rate has ever been fetched successfully.
    """
    import xml.etree.ElementTree as ET

    import requests

    now = datetime.now(UTC)
    cached_rate = _fx_cache["rate"]
    fetched_at = _fx_cache["fetched_at"]
    if cached_rate and fetched_at and (now - fetched_at).total_seconds() < _FX_CACHE_TTL_SECONDS:
        return cached_rate

    try:
        # TCMB rejects requests with the default python-requests User-Agent
        # (looks like a bot); a browser-like one is required for a 200.
        resp = requests.get(
            _TCMB_TODAY_XML_URL,
            timeout=10,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            },
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        node = root.find(".//Currency[@Kod='USD']/ForexSelling")
        rate = float((node.text or "").strip().replace(",", "."))
        if rate <= 0:
            raise ValueError(f"non-positive TCMB USD rate: {rate!r}")
    except Exception as exc:
        logger.error("TCMB USD/TRY rate fetch failed: %s", exc)
        if cached_rate:
            return cached_rate
        raise ForexRateUnavailable("Could not determine the USD/TRY exchange rate") from exc

    _fx_cache["rate"] = rate
    _fx_cache["fetched_at"] = now
    return rate


def plan_amount_minor_units(plan_key: str, currency: str = "USD") -> int:
    """Price for a plan in the smallest currency unit (e.g. cents/kurus).

    TRY converts the USD list price using TCMB's daily selling rate (PayTR
    settles in TRY); any other currency keeps the legacy USD-equivalent
    behaviour (LemonSqueezy/Dev).
    """
    price_usd = get_license_plan(plan_key).price_usd
    if (currency or "USD").strip().upper() in {"TRY", "TL"}:
        return int(round(price_usd * get_usd_try_forex_selling_rate() * 100))
    return int(round(price_usd * 100))


def apply_successful_payment(payment, model, plan_key: str) -> None:
    """Mark ``payment`` paid and upgrade ``model`` to ``plan_key``.

    Idempotent: safe to call more than once for the same order (license fields
    are recomputed deterministically and ``paid_at`` is only set once).

    ``plan_key`` must be a buyable paid plan. Guarding here (not just at the
    webhook route) means every caller is protected: ``apply_model_license_defaults``
    silently normalises an unknown/empty plan to "free", so without this check a
    stray ``None`` would downgrade a paid model instead of failing loudly.
    """
    if plan_key not in PAID_PLAN_KEYS:
        raise ValueError(f"apply_successful_payment requires a paid plan, got {plan_key!r}")
    payment.status = "paid"
    if not payment.paid_at:
        payment.paid_at = datetime.now(UTC)
    if model is not None:
        # A paid upgrade/renewal grants a fresh access window measured from the
        # moment of payment. Reset the start so renewing an already-expired model
        # actually restores access (apply_model_license_defaults preserves an
        # existing access_starts_at, which would otherwise keep the model expired).
        model.access_starts_at = datetime.now(UTC)
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

    # plan_key -> config var holding that plan's LemonSqueezy variant id.
    _VARIANT_CONFIG_KEYS = {
        "academic": "LEMONSQUEEZY_VARIANT_ACADEMIC",
        "extended_archive": "LEMONSQUEEZY_VARIANT_EXTENDED",
    }

    def create_checkout(self, *, payment, model, plan_key, user, success_url, cancel_url):
        """Create a hosted LemonSqueezy checkout and return its URL.

        Gated by config exactly like PayTR: if the API key, store id, or the
        plan's variant id is missing, this returns ``None`` (the route then
        shows "online payment isn't configured yet"), so the provider is inert
        and safe to ship until credentials are set.

        ``custom`` carries payment_id/model_id/plan_key so :meth:`parse_event`
        can map the webhook back to the right model. ``custom_price`` is sent so
        an in-app coupon discount (already computed into ``payment.amount_kurus``)
        is honoured at checkout rather than falling back to the variant's list
        price.
        """
        import requests

        cfg = current_app.config
        api_key = cfg.get("LEMONSQUEEZY_API_KEY")
        store_id = cfg.get("LEMONSQUEEZY_STORE_ID")
        variant_id = cfg.get(self._VARIANT_CONFIG_KEYS.get(plan_key, ""))
        if not (api_key and store_id and variant_id):
            logger.warning(
                "LemonSqueezy is not fully configured (need LEMONSQUEEZY_API_KEY, "
                "LEMONSQUEEZY_STORE_ID and the variant id for plan %r); returning None.",
                plan_key,
            )
            return None

        attributes = {
            "checkout_data": {
                "email": user.email,
                "custom": {
                    "payment_id": str(payment.id),
                    "model_id": str(model.id),
                    "plan_key": plan_key,
                },
            },
            "product_options": {
                "redirect_url": success_url,
            },
        }
        # Honour an in-app discount. amount_kurus is in USD cents for the
        # LemonSqueezy (USD Merchant-of-Record) path; only override when it is a
        # positive amount that differs from letting the variant price stand.
        if payment.amount_kurus and int(payment.amount_kurus) > 0:
            attributes["custom_price"] = int(payment.amount_kurus)

        body = {
            "data": {
                "type": "checkouts",
                "attributes": attributes,
                "relationships": {
                    "store": {"data": {"type": "stores", "id": str(store_id)}},
                    "variant": {"data": {"type": "variants", "id": str(variant_id)}},
                },
            }
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
        }
        try:
            resp = requests.post(
                "https://api.lemonsqueezy.com/v1/checkouts",
                json=body,
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # pragma: no cover - network dependent
            logger.error("LemonSqueezy create-checkout request failed: %s", exc)
            return None
        url = (((data or {}).get("data") or {}).get("attributes") or {}).get("url")
        if not url:
            logger.error("LemonSqueezy checkout response had no URL: %s", data)
            return None
        return url

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
        # Gate on the order's ACTUAL status, not the event name: order_created can
        # fire with status 'pending'/'failed', so trusting the event name alone
        # would grant a multi-year license before the payment has settled.
        paid = raw_status in {"paid", "completed", "active"}
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
        try:
            amount_minor = int(request.form.get("total_amount", ""))
        except (TypeError, ValueError):
            amount_minor = None
        return {
            "provider_reference": request.form.get("merchant_oid") or None,
            "status": "paid" if status == "success" else (status or "pending"),
            "plan_key": None,  # recovered from the stored Payment.plan_key
            "model_id": None,  # recovered from the stored Payment.model_id
            "payment_id": None,
            # Captured amount in minor units (kuruş). Verified against the stored
            # Payment.amount_kurus so an underpaid callback cannot grant a plan.
            "amount_minor": amount_minor,
        }


_PROVIDERS: dict[str, PaymentProvider] = {
    p.name: p for p in (DevProvider(), LemonSqueezyProvider(), PayTRProvider())
}


def get_payment_provider() -> PaymentProvider:
    """Resolve the active provider from ``PAYMENT_PROVIDER`` config.

    Falls back to the dev provider when ``ALLOW_DEV_PAYMENTS`` is on (local/test)
    and to LemonSqueezy otherwise so production never silently grants free
    upgrades. Critically, an *unrecognized* provider name (e.g. a typo like
    "lemonsqeezy") is treated the same as unset — it must NOT fall through to the
    dev provider, whose webhook verification accepts anything, which would let a
    forged "paid" callback grant free licenses in production.
    """
    name = (current_app.config.get("PAYMENT_PROVIDER") or "").strip().lower()
    if name not in _PROVIDERS:
        name = "development" if current_app.config.get("ALLOW_DEV_PAYMENTS") else "lemonsqueezy"
    return _PROVIDERS[name]
