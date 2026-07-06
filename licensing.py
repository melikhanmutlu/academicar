"""Model-based licensing helpers for AcademicAR."""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LicensePlan:
    key: str
    label: str
    price_usd: float
    duration_days: int | None
    storage_limit_bytes: int
    feature_summary: tuple[str, ...]
    # Admin-editable via /admin/pricing. Additionally gates upgrade/renewal
    # offers on top of USER_SELECTABLE_PLAN_KEYS/PAID_PLAN_KEYS (which decide
    # which keys exist as buyable concepts at all) — this only narrows further,
    # it never widens what those allowlists permit.
    is_purchasable: bool = True

    @property
    def is_paid(self) -> bool:
        return self.price_usd > 0


MB = 1024 * 1024

# Python-side fallback values, used to seed the license_plans DB table on
# first run and as the cache's baseline whenever a key has no matching row
# (e.g. in every test, whose conftest empties the table after create_app()).
_DEFAULT_LICENSE_PLANS: dict[str, LicensePlan] = {
    "free": LicensePlan(
        key="free",
        label="Free Access",
        price_usd=0,
        duration_days=3,
        storage_limit_bytes=100 * MB,
        feature_summary=(
            "3-day AR and QR access",
            "1 interactive model",
            "Screenshot export",
            "Video recording",
            "Watermarked viewer",
        ),
    ),
    "academic": LicensePlan(
        key="academic",
        label="Academic",
        price_usd=9.90,
        duration_days=365 * 3,
        storage_limit_bytes=200 * MB,
        feature_summary=(
            "3-year AR and QR access",
            "1 interactive model",
            "Screenshot export",
            "Video recording",
            "No watermark",
            "Persistent QR and viewer URL",
        ),
    ),
    "extended_archive": LicensePlan(
        key="extended_archive",
        label="Extended Archive",
        price_usd=24.90,
        duration_days=365 * 10,
        storage_limit_bytes=200 * MB,
        feature_summary=(
            "10-year AR and QR access",
            "Priority archival storage",
            "Guided viewing",
            "Saved camera views",
            "Rich metadata fields",
            "Persistent QR and viewer URL",
        ),
    ),
    "institutional": LicensePlan(
        key="institutional",
        label="Institutional",
        price_usd=0,
        duration_days=None,
        storage_limit_bytes=500 * MB,
        feature_summary=(
            "Unlimited AR and QR access",
            "Bulk model conversions",
            "SSO Integration",
            "Custom subdomain",
            "Dedicated support",
        ),
        is_purchasable=False,
    ),
}

_CACHE_TTL_SECONDS = 60
_cache_lock = threading.Lock()
_cache_loaded_at = 0.0

# Live cache — SAME dict object for the life of the process. Other modules do
# `from licensing import LICENSE_PLANS`; refreshes MUST mutate this dict in
# place (.clear()/.update()) and must NEVER do `LICENSE_PLANS = {...}`, or
# every module that already imported the name keeps a permanently stale copy.
LICENSE_PLANS: dict[str, LicensePlan] = dict(_DEFAULT_LICENSE_PLANS)


def default_license_plans() -> dict[str, LicensePlan]:
    """Defensive copy of the Python-side fallback, used by app.seed_license_plans."""
    return dict(_DEFAULT_LICENSE_PLANS)


def _plan_from_row(row, base: LicensePlan | None) -> LicensePlan:
    fallback = base or LicensePlan(
        key=row.key, label=row.label, price_usd=0, duration_days=None,
        storage_limit_bytes=0, feature_summary=(), is_purchasable=True,
    )
    return dataclasses.replace(
        fallback,
        key=row.key,
        label=row.label,
        price_usd=row.price_usd_cents / 100.0,
        duration_days=row.duration_days,
        storage_limit_bytes=row.storage_limit_bytes,
        is_purchasable=row.is_purchasable,
    )


def _reload_cache_from_db() -> None:
    from models import LicensePlanConfig

    merged = dict(_DEFAULT_LICENSE_PLANS)
    try:
        rows = LicensePlanConfig.query.all()
    except Exception:
        # No app context yet, DB unreachable, table not migrated yet, etc. --
        # this runs from inject_globals on every page, so a transient DB
        # hiccup here must never break an unrelated page render.
        logger.exception("license plan cache refresh failed; keeping previous values")
        return
    for row in rows:
        merged[row.key] = _plan_from_row(row, _DEFAULT_LICENSE_PLANS.get(row.key))
    LICENSE_PLANS.clear()
    LICENSE_PLANS.update(merged)


def _ensure_cache_fresh() -> None:
    global _cache_loaded_at
    if time.monotonic() - _cache_loaded_at < _CACHE_TTL_SECONDS:
        return
    with _cache_lock:
        if time.monotonic() - _cache_loaded_at < _CACHE_TTL_SECONDS:
            return
        _reload_cache_from_db()
        _cache_loaded_at = time.monotonic()


def refresh_license_plan_cache() -> None:
    """Eager, synchronous refresh — call right after an admin edit commits."""
    global _cache_loaded_at
    _reload_cache_from_db()
    _cache_loaded_at = time.monotonic()


def get_license_plans() -> dict[str, LicensePlan]:
    _ensure_cache_fresh()
    return LICENSE_PLANS


# Single source of truth for the plans a user can self-select / be assigned
# (a subset of LICENSE_PLANS — "institutional" is provisioned manually, not
# user-selectable). Used by the profile page and the admin plan controls so
# they never drift apart.
USER_SELECTABLE_PLAN_KEYS: tuple[str, ...] = ("free", "academic", "extended_archive")


def is_valid_user_plan(value: str | None) -> bool:
    return (value or "").strip().lower() in USER_SELECTABLE_PLAN_KEYS


def normalize_license_type(value: str | None) -> str:
    key = (value or "free").strip().lower()
    return key if key in LICENSE_PLANS else "free"


def get_license_plan(value: str | None) -> LicensePlan:
    _ensure_cache_fresh()
    return LICENSE_PLANS[normalize_license_type(value)]


def license_expires_at(license_type: str | None, starts_at: datetime | None = None) -> datetime | None:
    plan = get_license_plan(license_type)
    if plan.duration_days is None:
        return None
    start = starts_at or datetime.now(UTC)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return start + timedelta(days=plan.duration_days)


def is_access_expired(expires_at: datetime | None) -> bool:
    if not expires_at:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < datetime.now(UTC)


def apply_model_license_defaults(model, license_type: str | None = None) -> None:
    """Snap a Model3D's license fields to the canonical values for its plan.

    Idempotent: starting time is preserved if already set, but expiry/limit are
    always recomputed so license upgrades and renewals stay consistent.
    """
    plan = get_license_plan(license_type if license_type is not None else model.license_type)
    model.license_type = plan.key
    if not model.access_starts_at:
        model.access_starts_at = datetime.now(UTC)
    model.access_expires_at = license_expires_at(plan.key, model.access_starts_at)
    model.storage_limit_bytes = plan.storage_limit_bytes
    model.license_status = "active"


def model_access_status(model) -> str:
    """Return one of: active, queued, processing, failed, replacement_failed,
    expired, deleted. Used by the QR resolver and the public viewer."""
    if model is None:
        return "deleted"
    proc = (model.processing_status or "ready").lower()
    if proc in {"queued", "processing"}:
        return proc
    if proc == "failed":
        return "failed"
    if proc == "replacement_failed":
        # The previous working GLB is still served; treat as active for QR.
        return "active"
    if is_access_expired(model.access_expires_at):
        return "expired"
    return "active"


def model_is_accessible(model) -> bool:
    return model_access_status(model) == "active"


def paper_is_expired(paper) -> bool:
    return False


def model_file_limit_error(file_size: int, license_type: str | None) -> str | None:
    """Return a user-facing error if file_size exceeds the plan's per-model
    storage limit, else None."""
    plan = get_license_plan(license_type)
    if file_size > plan.storage_limit_bytes:
        limit_mb = plan.storage_limit_bytes / (1024 * 1024)
        return (
            f"Model file is too large for the {plan.label} plan "
            f"({limit_mb:.0f} MB limit)."
        )
    return None


def model_upgrade_options(model, within_days: int = 30) -> list[dict]:
    """Self-serve checkout options to show on a model's license CTA.

    Returns a list of ``{"key", "plan", "kind"}`` dicts where ``kind`` is:

    * ``"upgrade"`` — a strictly higher-priced paid plan; always offered.
    * ``"renew"``   — the model's *current* paid plan; offered only when access
      is already expired or lapses within ``within_days`` days.

    A downgrade (a cheaper plan than the current one) is never offered, and an
    Extended Archive model with years of access left returns ``[]`` so the
    caller can hide the whole block. This keeps the card from advertising plans
    the model already has (or worse, a downgrade) — the source of the
    "I upgraded but it still shows upgrade options" UX bug.
    """
    if model is None:
        return []
    current = get_license_plan(getattr(model, "license_type", None))
    options: list[dict] = []
    # Upgrades: any user-selectable plan priced above the current one, in
    # ascending price order (USER_SELECTABLE_PLAN_KEYS is free -> academic ->
    # extended_archive).
    for key in USER_SELECTABLE_PLAN_KEYS:
        plan = get_license_plan(key)
        if plan.price_usd > current.price_usd and plan.is_purchasable:
            options.append({"key": key, "plan": plan, "kind": "upgrade"})
    # Renew: only the current plan, only when it is paid, purchasable, and lapsing.
    if current.price_usd > 0 and current.is_purchasable:
        exp = getattr(model, "access_expires_at", None)
        lapsing = exp is None or is_access_expired(exp)
        if not lapsing and exp is not None:
            e = exp if exp.tzinfo else exp.replace(tzinfo=UTC)
            lapsing = e <= datetime.now(UTC) + timedelta(days=within_days)
        if lapsing:
            options.append({"key": current.key, "plan": current, "kind": "renew"})
    return options
