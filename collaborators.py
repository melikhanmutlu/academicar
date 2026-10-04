"""Project collaborators (co-authors, lab members).

Roles: the project owner, and editors. Editors can edit the project's
content: metadata, models (upload, replace, appearance, labels, scale),
PDFs and materials. Deleting things, visibility, managing collaborators and
payments stay with the owner, and every model belongs to the owner (an
editor's upload is recorded in ``Model3D.uploaded_by_user_id``).

Invites go by email. An existing account is added straight away; an unknown
address waits as a pending row until someone signs up with it AND confirms
it (otherwise anyone could register a colleague's address to get in).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from flask_login import current_user

from models import Paper, ProjectCollaborator, User, db

logger = logging.getLogger(__name__)

MAX_COLLABORATORS_PER_PROJECT = 20


def project_role(paper: Paper | None, user=None) -> str | None:
    """'owner', 'editor' or None for ``user`` (default: the current user)."""
    user = user if user is not None else current_user
    if paper is None or user is None or not getattr(user, "is_authenticated", False):
        return None
    if paper.user_id == user.id:
        return "owner"
    accepted = ProjectCollaborator.query.filter_by(paper_id=paper.id, user_id=user.id).first()
    return "editor" if accepted is not None else None


def can_edit_project(paper: Paper | None, user=None) -> bool:
    return project_role(paper, user) in ("owner", "editor")


def shared_projects_for(user) -> list[Paper]:
    """Projects other people shared with ``user`` (not deleted)."""
    from sqlalchemy import or_

    return (
        Paper.query.join(ProjectCollaborator, ProjectCollaborator.paper_id == Paper.id)
        .filter(
            ProjectCollaborator.user_id == user.id,
            or_(Paper.status.is_(None), Paper.status != "deleted"),
        )
        .order_by(Paper.created_at.desc())
        .all()
    )


def add_collaborator(paper: Paper, email: str, invited_by) -> tuple[ProjectCollaborator | None, str]:
    """Invite ``email`` as an editor. Returns (row, outcome) where outcome is
    'added', 'pending', 'exists', 'self' or 'limit'."""
    email = (email or "").strip().lower()
    owner = db.session.get(User, paper.user_id)
    if owner is not None and owner.email.lower() == email:
        return None, "self"
    existing = ProjectCollaborator.query.filter_by(paper_id=paper.id, email=email).first()
    if existing is not None:
        return existing, "exists"
    if ProjectCollaborator.query.filter_by(paper_id=paper.id).count() >= MAX_COLLABORATORS_PER_PROJECT:
        return None, "limit"
    user = User.query.filter(db.func.lower(User.email) == email).first()
    row = ProjectCollaborator(paper_id=paper.id, email=email, invited_by_user_id=invited_by.id)
    # Only a confirmed address is attached right away.
    if user is not None and user.is_email_verified:
        row.user_id = user.id
        row.accepted_at = datetime.now(UTC)
    db.session.add(row)
    db.session.commit()
    return row, ("added" if row.user_id else "pending")


def claim_pending_collaborations(user) -> int:
    """Attach pending invites sent to ``user``'s confirmed address."""
    if user is None or not user.is_email_verified:
        return 0
    rows = ProjectCollaborator.query.filter(
        ProjectCollaborator.user_id.is_(None),
        db.func.lower(ProjectCollaborator.email) == user.email.lower(),
    ).all()
    for row in rows:
        if row.paper is not None and row.paper.user_id == user.id:
            db.session.delete(row)  # invited to their own project
            continue
        row.user_id = user.id
        row.accepted_at = datetime.now(UTC)
    if rows:
        db.session.commit()
    return len(rows)


def send_collaborator_email(row: ProjectCollaborator, inviter) -> bool:
    """Tell the invitee. Best effort, like the other account emails."""
    from url_helpers import public_url
    from utils.email import send_email

    paper = row.paper
    who = inviter.username or inviter.email
    if row.user_id:
        link = public_url("project_detail", slug=paper.slug)
        body = (
            f"{who} added you as an editor of the AcademicAR project \"{paper.title}\".\n\n"
            "You can edit its details, upload and update its 3D models and labels, and add PDFs:\n"
            f"{link}\n\nIt also appears under \"Shared with you\" on your dashboard."
        )
    else:
        link = public_url("auth.register", next=f"/projects/{paper.slug}")
        body = (
            f"{who} invited you to edit the AcademicAR project \"{paper.title}\".\n\n"
            f"Sign in or create a free account with this email address ({row.email}) and confirm it; the "
            f"project then appears under \"Shared with you\" on your dashboard:\n{link}"
        )
    try:
        return bool(send_email(row.email, f"{who} shared \"{paper.title}\" with you on AcademicAR", body))
    except Exception:
        logger.exception("collaborator email failed for project %s", paper.id)
        return False
