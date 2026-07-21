"""Institutional (B2B) membership and quota helpers.

An Institution is created and priced manually by the platform admin (offline
contract + invoicing). Members join via invite links (institution_panel.py).
While the contract is current and the quota is not exhausted, a member's
uploads are granted the "institutional" license plan; otherwise they fall
back to the normal free flow. All quota math lives here so the upload hook
in app.py stays a two-line decision. The monthly usage report emails also
live here (sent from the worker loop, never from a web request).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from licensing import apply_model_license_defaults
from models import AuditLog, Institution, InstitutionMember, Model3D, Paper, User, db

logger = logging.getLogger(__name__)


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


def _funded_models_query(institution_id):
    """Base query for an institution's funded, non-deleted models."""
    return (
        Model3D.query
        .join(Paper, Model3D.paper_id == Paper.id)
        .filter(
            Model3D.institution_id == institution_id,
            Model3D.license_type == "institutional",
            or_(Paper.status.is_(None), Paper.status != "deleted"),
        )
    )


def institution_recent_activity(institution_id, member_limit=5, model_limit=5, now=None) -> dict:
    """Overview-page activity feed for an institution. A handful of small,
    indexed queries (this feeds a panel page, not a hot request path):
    recent members, recently funded models, models added in the last 30 days,
    and the top model contributors."""
    now = now or datetime.now(UTC)

    recent_members = (
        InstitutionMember.query.options(selectinload(InstitutionMember.user))
        .filter_by(institution_id=institution_id)
        .order_by(InstitutionMember.joined_at.desc())
        .limit(member_limit)
        .all()
    )

    recent_models = (
        _funded_models_query(institution_id)
        .options(selectinload(Model3D.paper), selectinload(Model3D.user))
        .order_by(Model3D.created_at.desc())
        .limit(model_limit)
        .all()
    )

    models_last_30d = (
        _funded_models_query(institution_id)
        .filter(Model3D.created_at >= now - timedelta(days=30))
        .count()
    )

    contributor_rows = (
        db.session.query(User, func.count(Model3D.id).label("model_count"))
        .join(Model3D, Model3D.user_id == User.id)
        .join(Paper, Model3D.paper_id == Paper.id)
        .filter(
            Model3D.institution_id == institution_id,
            Model3D.license_type == "institutional",
            or_(Paper.status.is_(None), Paper.status != "deleted"),
        )
        .group_by(User.id)
        .order_by(func.count(Model3D.id).desc())
        .limit(5)
        .all()
    )
    top_contributors = [{"user": user, "model_count": int(count)} for user, count in contributor_rows]

    return {
        "recent_members": recent_members,
        "recent_models": recent_models,
        "models_last_30d": models_last_30d,
        "top_contributors": top_contributors,
    }


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


def _month_key(value: datetime) -> tuple[int, int]:
    return (value.year, value.month)


def _report_is_due(institution, now: datetime) -> bool:
    """A report goes out once per calendar month, starting the month AFTER
    the institution was created (a creation-day report has nothing to say)."""
    last = institution.last_usage_report_at
    if last is not None:
        return _month_key(last) < _month_key(now)
    created = institution.created_at or now
    return _month_key(created) < _month_key(now)


def _compose_usage_report(institution, now: datetime) -> str:
    model_count, bytes_used = institution_usage(institution.id)
    member_count = InstitutionMember.query.filter_by(institution_id=institution.id).count()
    added_last_30d = (
        Model3D.query.filter(
            Model3D.institution_id == institution.id,
            Model3D.license_type == "institutional",
            Model3D.created_at >= now - timedelta(days=30),
        ).count()
    )

    def fmt_mb(num_bytes):
        return f"{num_bytes / (1024 * 1024):.1f} MB"

    lines = [
        f"AcademicAR monthly usage report — {institution.name}",
        f"Period: up to {now.strftime('%Y-%m-%d')}",
        "",
        f"Members: {member_count}",
        f"Institution-funded models: {model_count}"
        + (f" / {institution.quota_model_count}" if institution.quota_model_count is not None else ""),
        f"Storage used: {fmt_mb(bytes_used)}"
        + (f" / {fmt_mb(institution.quota_storage_bytes)}" if institution.quota_storage_bytes is not None else ""),
        f"Models added in the last 30 days: {added_last_30d}",
        "",
        "Contract: "
        + (institution.contract_starts_at.strftime("%Y-%m-%d") if institution.contract_starts_at else "—")
        + " -> "
        + (institution.contract_ends_at.strftime("%Y-%m-%d") if institution.contract_ends_at else "open-ended"),
        f"Status: {institution.status}",
        "",
        "Manage members, invites, and models: see the Institution panel under your account.",
    ]
    return "\n".join(lines)


def send_monthly_institution_reports(now: datetime | None = None) -> int:
    """Email each active institution's admins a usage summary once per
    calendar month. Called from the worker loop (never a web request).
    Returns the number of institutions a report was composed for.

    last_usage_report_at is stamped even if delivery fails or no mail
    backend is configured — the report is best-effort and must not retry
    every poll cycle.
    """
    from utils.email import send_email

    now = now or datetime.now(UTC)
    sent = 0
    institutions = Institution.query.filter_by(status="active").all()
    for institution in institutions:
        if not _report_is_due(institution, now):
            continue
        admin_members = (
            InstitutionMember.query.filter_by(institution_id=institution.id, role="admin").all()
        )
        recipients = [m.user.email for m in admin_members if m.user and m.user.email]
        institution.last_usage_report_at = now
        if not recipients:
            continue
        body = _compose_usage_report(institution, now)
        delivered = False
        for recipient in recipients:
            try:
                if send_email(recipient, f"AcademicAR usage report — {institution.name}", body):
                    delivered = True
            except Exception:
                logger.exception("usage report email failed for institution %s", institution.id)
        db.session.add(
            AuditLog(
                event_type="institution_usage_report_sent",
                resource_id=str(institution.id),
                details={"recipients": len(recipients), "delivered": delivered},
            )
        )
        sent += 1
    if institutions:
        db.session.commit()
    return sent


def send_contract_renewal_reminders(now: datetime | None = None) -> int:
    """Email institution admins (and the platform contact) when a contract
    ends within 30 days. One reminder per contract end date: the stamp
    records WHICH contract_ends_at was announced, so renewing to a later
    date re-arms the reminder automatically. Called from the worker loop.
    Stamped even if delivery fails, mirroring the monthly reports."""
    from flask import current_app

    from utils.email import send_email

    now = now or datetime.now(UTC)
    window_end = now + timedelta(days=30)
    sent = 0
    institutions = (
        Institution.query.filter(
            Institution.status == "active",
            Institution.contract_ends_at.isnot(None),
            Institution.contract_ends_at >= now.replace(tzinfo=None),
            Institution.contract_ends_at <= window_end.replace(tzinfo=None),
        ).all()
    )
    for institution in institutions:
        if institution.renewal_reminder_sent_for == institution.contract_ends_at:
            continue
        model_count, bytes_used = institution_usage(institution.id)
        ends = institution.contract_ends_at.strftime("%Y-%m-%d")
        body = (
            f"The AcademicAR institutional contract for {institution.name} "
            f"ends on {ends}.\n\n"
            f"Current usage: {model_count} funded model(s), "
            f"{bytes_used / (1024 * 1024):.1f} MB storage.\n\n"
            "Models funded by the contract lose public AR/QR access when it "
            "expires. Contact the AcademicAR team to renew."
        )
        subject = f"AcademicAR contract renewal — {institution.name} ends {ends}"
        admin_members = (
            InstitutionMember.query.filter_by(institution_id=institution.id, role="admin").all()
        )
        recipients = [m.user.email for m in admin_members if m.user and m.user.email]
        contact = (current_app.config.get("CONTACT_EMAIL") or "").strip()
        if contact:
            recipients.append(contact)
        institution.renewal_reminder_sent_for = institution.contract_ends_at
        delivered = False
        for recipient in recipients:
            try:
                if send_email(recipient, subject, body):
                    delivered = True
            except Exception:
                logger.exception("renewal reminder email failed for institution %s", institution.id)
        db.session.add(
            AuditLog(
                event_type="institution_renewal_reminder_sent",
                resource_id=str(institution.id),
                details={"contract_ends_at": ends, "recipients": len(recipients), "delivered": delivered},
            )
        )
        sent += 1
    if institutions:
        db.session.commit()
    return sent


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
        .update(
            {"access_expires_at": now, "license_status": "expired"},
            synchronize_session="fetch",
        )
    )
    return int(updated or 0)
