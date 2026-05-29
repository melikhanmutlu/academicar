"""Model-based licensing helpers for AcademicAR."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class LicensePlan:
    key: str
    label: str
    price_usd: float
    duration_days: int | None
    storage_limit_bytes: int
    feature_summary: tuple[str, ...]

    @property
    def is_paid(self) -> bool:
        return self.price_usd > 0


MB = 1024 * 1024

LICENSE_PLANS: dict[str, LicensePlan] = {
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
    ),
}


def normalize_license_type(value: str | None) -> str:
    key = (value or "free").strip().lower()
    return key if key in LICENSE_PLANS else "free"


def get_license_plan(value: str | None) -> LicensePlan:
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
    if not paper or not paper.expires_at:
        return False
    expires_at = paper.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < datetime.now(UTC)


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
