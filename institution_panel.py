"""Institution panel blueprint: the B2B customer's own surface.

Institution admins (InstitutionMember.role == "admin") manage members and
invite links and watch contract/quota usage here — separate from the
platform /admin panel, which stays platform-staff only. The public invite
join route also lives here so the whole membership lifecycle is in one
module. Registered in create_app right after auth_bp.
"""

import secrets
from datetime import UTC, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_limiter.util import get_remote_address
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload

from extensions import limiter
from institutions import institution_usage
from licensing import is_access_expired
from models import Institution, InstitutionInvite, InstitutionMember, Model3D, Paper, db
from url_helpers import public_url

institution_bp = Blueprint("institution", __name__, url_prefix="/institution")

PANEL_PER_PAGE = 25


def _log_audit(event_type, **kwargs):
    from app import log_audit  # lazy: avoids app <-> blueprint circular import

    log_audit(event_type, **kwargs)


def _require_institution_admin() -> tuple[InstitutionMember, Institution]:
    """403 unless the current user is an institution admin. Platform admins
    get no bypass — they manage institutions from /admin instead."""
    if not current_user.is_authenticated:
        abort(403)
    membership = InstitutionMember.query.filter_by(user_id=current_user.id, role="admin").first()
    if membership is None:
        abort(403)
    return membership, membership.institution


def _invite_state(invite) -> str:
    if invite.revoked_at is not None:
        return "revoked"
    if is_access_expired(invite.expires_at):
        return "expired"
    if invite.max_uses is not None and invite.use_count >= invite.max_uses:
        return "exhausted"
    return "active"


def _load_valid_invite(token: str):
    """The invite row for an acceptable token, else None. Callers must render
    ONE generic failure regardless of which condition failed — never reveal
    whether a token exists, is revoked, expired, or exhausted."""
    if not token or len(token) > 64:
        return None
    invite = InstitutionInvite.query.filter_by(token=token).first()
    if invite is None:
        return None
    if _invite_state(invite) != "active":
        return None
    if invite.institution.status != "active":
        return None
    return invite


@institution_bp.route("/")
@login_required
def overview():
    membership, institution = _require_institution_admin()
    model_count, bytes_used = institution_usage(institution.id)
    member_count = InstitutionMember.query.filter_by(institution_id=institution.id).count()
    active_invites = sum(
        1
        for invite in InstitutionInvite.query.filter_by(institution_id=institution.id, revoked_at=None).all()
        if _invite_state(invite) == "active"
    )
    return render_template(
        "institution/overview.html",
        institution=institution,
        membership=membership,
        usage_models=model_count,
        usage_bytes=bytes_used,
        member_count=member_count,
        active_invites=active_invites,
        active_tab="overview",
    )


@institution_bp.route("/members")
@login_required
def members():
    membership, institution = _require_institution_admin()
    page = max(request.args.get("page", type=int) or 1, 1)
    pagination = (
        InstitutionMember.query.options(selectinload(InstitutionMember.user))
        .filter_by(institution_id=institution.id)
        .order_by(InstitutionMember.joined_at.asc())
        .paginate(page=page, per_page=PANEL_PER_PAGE, error_out=False)
    )
    return render_template(
        "institution/members.html",
        institution=institution,
        membership=membership,
        members_pagination=pagination,
        active_tab="members",
    )


@institution_bp.route("/members/<int:member_id>/remove", methods=["POST"])
@login_required
def member_remove(member_id):
    membership, institution = _require_institution_admin()
    member = InstitutionMember.query.filter_by(id=member_id, institution_id=institution.id).first()
    if member is None:
        abort(404)
    if member.user_id == current_user.id:
        flash("You cannot remove yourself. Ask the platform team if you need to leave.", "danger")
        return redirect(url_for("institution.members"))
    removed_user_id = member.user_id
    db.session.delete(member)
    db.session.commit()
    _log_audit(
        "institution_member_removed",
        user_id=current_user.id,
        resource_id=str(institution.id),
        details={"member_user_id": removed_user_id, "removed_by": "institution_admin"},
    )
    flash("Member removed. Models they already uploaded keep their institutional access.", "success")
    return redirect(url_for("institution.members"))


@institution_bp.route("/members/<int:member_id>/role", methods=["POST"])
@login_required
def member_role(member_id):
    membership, institution = _require_institution_admin()
    member = InstitutionMember.query.filter_by(id=member_id, institution_id=institution.id).first()
    if member is None:
        abort(404)
    new_role = (request.form.get("role") or "").strip().lower()
    if new_role not in {"member", "admin"}:
        flash("Invalid role.", "danger")
        return redirect(url_for("institution.members"))
    if member.user_id == current_user.id and new_role != "admin":
        # Lockout guard: the last admin demoting themselves would orphan the
        # panel; platform admins are the recovery path, so just refuse.
        flash("You cannot remove your own admin role.", "danger")
        return redirect(url_for("institution.members"))
    previous = member.role
    member.role = new_role
    db.session.commit()
    _log_audit(
        "institution_member_role_changed",
        user_id=current_user.id,
        resource_id=str(institution.id),
        details={"member_user_id": member.user_id, "from": previous, "to": new_role},
    )
    flash("Member role updated.", "success")
    return redirect(url_for("institution.members"))


@institution_bp.route("/invites")
@login_required
def invites():
    membership, institution = _require_institution_admin()
    invite_rows = (
        InstitutionInvite.query.filter_by(institution_id=institution.id)
        .order_by(InstitutionInvite.created_at.desc())
        .all()
    )
    invites_with_state = [
        {
            "invite": invite,
            "state": _invite_state(invite),
            "join_url": public_url("institution.join", token=invite.token),
        }
        for invite in invite_rows
    ]
    return render_template(
        "institution/invites.html",
        institution=institution,
        membership=membership,
        invites=invites_with_state,
        active_tab="invites",
    )


@institution_bp.route("/invites/create", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def invite_create():
    membership, institution = _require_institution_admin()
    expires_raw = (request.form.get("expires_days") or "").strip()
    expires_at = None
    if expires_raw:
        if not expires_raw.isdigit() or int(expires_raw) < 1 or int(expires_raw) > 365:
            flash("Invite validity must be between 1 and 365 days (or blank for no expiry).", "danger")
            return redirect(url_for("institution.invites"))
        expires_at = datetime.now(UTC) + timedelta(days=int(expires_raw))
    max_uses_raw = (request.form.get("max_uses") or "").strip()
    max_uses = None
    if max_uses_raw:
        if not max_uses_raw.isdigit() or int(max_uses_raw) < 1:
            flash("Max uses must be a positive number (or blank for unlimited).", "danger")
            return redirect(url_for("institution.invites"))
        max_uses = int(max_uses_raw)
    invite = InstitutionInvite(
        institution_id=institution.id,
        token=secrets.token_urlsafe(24),
        created_by_user_id=current_user.id,
        expires_at=expires_at,
        max_uses=max_uses,
    )
    db.session.add(invite)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        flash("Could not create the invite. Please try again.", "danger")
        return redirect(url_for("institution.invites"))
    _log_audit(
        "institution_invite_created",
        user_id=current_user.id,
        resource_id=str(institution.id),
        details={"invite_id": invite.id, "max_uses": max_uses, "expires_at": expires_at.isoformat() if expires_at else None},
    )
    flash("Invite link created. Copy it from the list below.", "success")
    return redirect(url_for("institution.invites"))


@institution_bp.route("/invites/<int:invite_id>/revoke", methods=["POST"])
@login_required
def invite_revoke(invite_id):
    membership, institution = _require_institution_admin()
    invite = InstitutionInvite.query.filter_by(id=invite_id, institution_id=institution.id).first()
    if invite is None:
        abort(404)
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
        db.session.commit()
        _log_audit(
            "institution_invite_revoked",
            user_id=current_user.id,
            resource_id=str(institution.id),
            details={"invite_id": invite.id},
        )
    flash("Invite revoked.", "success")
    return redirect(url_for("institution.invites"))


@institution_bp.route("/models")
@login_required
def models_list():
    membership, institution = _require_institution_admin()
    page = max(request.args.get("page", type=int) or 1, 1)
    pagination = (
        Model3D.query.options(selectinload(Model3D.paper))
        .join(Paper, Model3D.paper_id == Paper.id)
        .filter(Model3D.institution_id == institution.id)
        .order_by(Model3D.created_at.desc())
        .paginate(page=page, per_page=PANEL_PER_PAGE, error_out=False)
    )
    return render_template(
        "institution/models.html",
        institution=institution,
        membership=membership,
        models_pagination=pagination,
        active_tab="models",
    )


@institution_bp.route("/join/<token>", methods=["GET", "POST"])
@limiter.limit("30 per hour", key_func=get_remote_address)
def join(token):
    invite = _load_valid_invite(token)
    if invite is None:
        # One generic response for every failure mode; 404 hides validity.
        return render_template("institution/join.html", invite=None), 404
    institution = invite.institution

    existing = (
        InstitutionMember.query.filter_by(user_id=current_user.id).first()
        if current_user.is_authenticated
        else None
    )

    if request.method == "POST":
        if not current_user.is_authenticated:
            abort(403)
        if existing is not None:
            flash(
                "You already belong to an institution."
                if existing.institution_id != institution.id
                else "You are already a member of this institution.",
                "warning",
            )
            return redirect(url_for("dashboard"))
        if not institution.email_matches_domains(current_user.email):
            domains = ", ".join("@" + d for d in institution.domain_list())
            flash(f"This invite requires an email address at: {domains}", "danger")
            return render_template(
                "institution/join.html",
                invite=invite,
                institution=institution,
                already_member=None,
                domain_mismatch=True,
            )
        member = InstitutionMember(
            institution_id=institution.id,
            user_id=current_user.id,
            role="member",
            invite_id=invite.id,
        )
        invite.use_count += 1
        db.session.add(member)
        try:
            db.session.commit()
        except IntegrityError:
            # Unique(user_id) race: two joins in flight for the same account.
            db.session.rollback()
            flash("You already belong to an institution.", "warning")
            return redirect(url_for("dashboard"))
        _log_audit(
            "institution_member_joined",
            user_id=current_user.id,
            resource_id=str(institution.id),
            details={"invite_id": invite.id},
        )
        flash(f"Welcome — you joined {institution.name}. New uploads are covered by its institutional license.", "success")
        return redirect(url_for("dashboard"))

    return render_template(
        "institution/join.html",
        invite=invite,
        institution=institution,
        already_member=existing,
        domain_mismatch=False,
    )
