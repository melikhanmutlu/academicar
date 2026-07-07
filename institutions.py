"""Institutional (B2B) membership and quota helpers.

An Institution is created and priced manually by the platform admin (offline
contract + invoicing). Members join via invite links (institution_panel.py).
While the contract is current and the quota is not exhausted, a member's
uploads are granted the "institutional" license plan; otherwise they fall
back to the normal free flow. All quota math lives here so the upload hook
in app.py stays a two-line decision.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_

from licensing import apply_model_license_defaults
from models import Institution, InstitutionMember, Model3D, Paper, db


def get_active_membership(user_id) -> InstitutionMember | None:
    """The user's institution membership, or None. v1 enforces one
    institution per user (UNIQUE on user_id), so first() is exact."""
    if not user_id:
        return None
    return InstitutionMember.query.filter_by(user_id=user_id).first()


def institution_usage(institution_id) -> tuple[int, int]:
    """(model_count, bytes_used) of the institution's funded models.

    Single aggregate query. Counts models still on the institutional plan
    (an admin re-licensing a model individually frees its quota) and joins
    Paper to exclude soft-deleted publications — the same visibility
    predicate the admin dashboard uses.
    """
    count, total = (
        db.session.query(
            func.count(Model3D.id),
            func.coalesce(func.sum(Model3D.file_size), 0),
        )
        .join(Paper, Model3D.paper_id == Paper.id)
        .filter(
            Model3D.institution_id == institution_id,
            Model3D.license_type == "institutional",
            or_(Paper.status.is_(None), Paper.status != "deleted"),
        )
        .one()
    )
    return int(count or 0), int(total or 0)


def institution_can_fund_upload(institution, file_size, usage=None) -> tuple[bool, str | None]:
    """Whether the institution's contract can cover one more model of
    file_size bytes. Returns (ok, reason); reason is a machine-readable slug
    for flash messaging and audit details."""
    if institution is None:
        return False, "no_institution"
    if institution.status != "active":
        return False, "suspended"
    if not institution.contract_is_current():
        return False, "contract_expired"
    if institution.quota_model_count is not None or institution.quota_storage_bytes is not None:
        model_count, bytes_used = usage if usage is not None else institution_usage(institution.id)
        if institution.quota_model_count is not None and model_count + 1 > institution.quota_model_count:
            return False, "quota_models"
        if institution.quota_storage_bytes is not None and bytes_used + file_size > institution.quota_storage_bytes:
            return False, "quota_storage"
    return True, None


def apply_institutional_license(model, institution) -> None:
    """Grant an institution-funded model the institutional plan.

    The single mutation point for institutional models: wraps
    apply_model_license_defaults (per-model storage limit, active status),
    then overrides the plan's unlimited duration with the contract end so
    access follows the contract, and stamps the funding institution.
    """
    apply_model_license_defaults(model, "institutional")
    model.access_expires_at = institution.contract_ends_at
    model.institution_id = institution.id


def reapply_model_license(model) -> None:
    """Re-snap a model's license fields to its CURRENT plan, preserving an
    institution-funded model's contract-bound expiry.

    Use this instead of apply_model_license_defaults(model, model.license_type)
    wherever license fields are re-applied without changing plan (e.g. after
    conversion finishes) — the plain call would reset an institutional model's
    access_expires_at to the plan's unlimited default, losing the contract end.
    """
    if model.license_type == "institutional" and model.institution_id is not None:
        institution = db.session.get(Institution, model.institution_id)
        if institution is not None:
            apply_institutional_license(model, institution)
            return
    apply_model_license_defaults(model, model.license_type)


def renew_institution_contract(institution, new_ends_at: datetime | None) -> int:
    """Set a new contract end date and propagate it to every model the
    institution funds. Returns the number of models updated.

    The license_type filter protects models an admin individually
    re-licensed (their expiry is no longer the contract's business).
    """
    institution.contract_ends_at = new_ends_at
    updated = (
        db.session.query(Model3D)
        .filter(
            Model3D.institution_id == institution.id,
            Model3D.license_type == "institutional",
        )
        .update({"access_expires_at": new_ends_at}, synchronize_session=False)
    )
    return int(updated or 0)


def end_institution_access_now(institution, now: datetime) -> int:
    """Hard cutoff: expire every institutional model of this institution
    immediately (suspension alone only blocks NEW uploads). Returns the
    number of models expired."""
    updated = (
        db.session.query(Model3D)
        .filter(
            Model3D.institution_id == institution.id,
            Model3D.license_type == "institutional",
        )
        .update({"access_expires_at": now}, synchronize_session=False)
    )
    return int(updated or 0)
