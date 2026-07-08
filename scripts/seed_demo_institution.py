#!/usr/bin/env python
"""Seed demo users, publications, and models for an EXISTING institution.

Populates a working demo of the institutional module: one institution admin
account, several member accounts, and for each of them a couple of
publications with a real (tiny, converted) 3D model — so every dashboard,
the institution panel, and the public showcase have something to show.

Runs against whatever database the app is configured for (DATABASE_URL, or
local SQLite if unset) — this is the SAME create_app() the web app and
worker use, so it must be run in an environment that can reach that
database (locally, or e.g. `railway run python scripts/seed_demo_institution.py`
against a deployed instance). It is safe to re-run: existing demo accounts
and their publications are reused rather than duplicated.

Usage:
    python scripts/seed_demo_institution.py                      # auto-picks the only institution
    python scripts/seed_demo_institution.py --institution 3
    python scripts/seed_demo_institution.py --institution "Bogazici University"
    python scripts/seed_demo_institution.py --members 5 --password "Demo1234!"

The upload pipeline (registration, paper creation, model upload) runs
through the real Flask routes via the test client, so conversion, QR
generation, and the institutional-licensing quota hook all behave exactly
as they would for a real user.
"""
from __future__ import annotations

import argparse
import io
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlalchemy import func  # noqa: E402

from app import create_app  # noqa: E402
from models import Institution, InstitutionMember, Model3D, Paper, User, db  # noqa: E402
from url_helpers import public_url  # noqa: E402

DEMO_STL = b"""solid tetra
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
facet normal 0 1 0
 outer loop
  vertex 0 0 0
  vertex 0 0 1
  vertex 1 0 0
 endloop
endfacet
facet normal 1 0 0
 outer loop
  vertex 0 0 0
  vertex 0 1 0
  vertex 0 0 1
 endloop
endfacet
facet normal 1 1 1
 outer loop
  vertex 1 0 0
  vertex 0 0 1
  vertex 0 1 0
 endloop
endfacet
endsolid tetra
"""

# "field" must match app.py's ACADEMIC_FIELDS allowlist exactly (Medicine,
# Dentistry, Engineering, Architecture, Biology, Archaeology,
# Veterinary Medicine, Chemistry, Other) or paper creation is rejected.
SAMPLE_PAPERS = [
    {
        "title": "Segmented Cranial Vault Reconstruction from CT",
        "field": "Medicine",
        "abstract": "A photogrammetry-derived mesh of a segmented cranial vault, prepared for classroom AR review.",
    },
    {
        "title": "Molecular Lattice Model of a Zeolite Framework",
        "field": "Chemistry",
        "abstract": "An interactive lattice model illustrating pore geometry in a microporous aluminosilicate.",
    },
    {
        "title": "Photogrammetric Scan of a Riverine Mollusk Shell",
        "field": "Biology",
        "abstract": "High-resolution shell geometry captured for comparative morphology teaching.",
    },
    {
        "title": "Reconstructed Amphora from a Coastal Excavation",
        "field": "Archaeology",
        "abstract": "A 3D reconstruction of a fragmentary amphora, annotated for a site report figure.",
    },
    {
        "title": "Facade Detail Model from a Heritage Building Survey",
        "field": "Architecture",
        "abstract": "A measured facade detail digitized during a heritage-building conservation survey.",
    },
    {
        "title": "Prototype Bracket for a Cantilever Load Test",
        "field": "Engineering",
        "abstract": "A machined bracket geometry used in an undergraduate structural-loading lab.",
    },
    {
        "title": "Comparative Limb Anatomy Model in a Quadruped Study",
        "field": "Veterinary Medicine",
        "abstract": "A limb-anatomy reconstruction prepared for a comparative locomotion study.",
    },
    {
        "title": "Intraoral Scan-Derived Model of Occlusal Anatomy",
        "field": "Dentistry",
        "abstract": "An intraoral scan converted into an interactive occlusal-anatomy teaching model.",
    },
    {
        "title": "Classroom Model of a Unit Cell Lattice",
        "field": "Other",
        "abstract": "A simplified unit-cell model used to introduce crystal lattice geometry to first-year students.",
    },
]


def resolve_institution_id(app, institution_arg: str | None) -> int:
    with app.app_context():
        if institution_arg:
            institution = None
            if institution_arg.isdigit():
                institution = db.session.get(Institution, int(institution_arg))
            if institution is None:
                institution = Institution.query.filter(
                    func.lower(Institution.name) == institution_arg.lower()
                ).first()
            if institution is None:
                print(f"No institution matches '{institution_arg}'.", file=sys.stderr)
                _list_institutions()
                sys.exit(1)
            return institution.id

        all_institutions = Institution.query.order_by(Institution.id).all()
        if len(all_institutions) == 1:
            return all_institutions[0].id
        if not all_institutions:
            print("No institutions exist yet. Create one from /admin/institutions first.", file=sys.stderr)
            sys.exit(1)
        print("Multiple institutions exist — pass --institution <id-or-name>:", file=sys.stderr)
        _list_institutions()
        sys.exit(1)


def _list_institutions() -> None:
    for institution in Institution.query.order_by(Institution.id).all():
        print(f"  [{institution.id}] {institution.name} (slug={institution.slug or '—'})", file=sys.stderr)


def ensure_user(email: str, username: str, password: str) -> tuple[int, bool]:
    user = User.query.filter(func.lower(User.email) == email.lower()).first()
    if user is not None:
        return user.id, False
    user = User(email=email.lower(), username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user.id, True


def ensure_membership(user_id: int, institution_id: int, role: str) -> str:
    existing = InstitutionMember.query.filter_by(user_id=user_id).first()
    if existing is not None:
        if existing.institution_id != institution_id:
            return "skipped — already a member of a different institution"
        if existing.role != role:
            existing.role = role
            db.session.commit()
        return "already a member"
    member = InstitutionMember(institution_id=institution_id, user_id=user_id, role=role)
    db.session.add(member)
    db.session.commit()
    return "joined"


def seed_papers_for_user(
    client, email: str, password: str, display_name: str, papers_per_user: int, pool_offset: int
) -> list[str]:
    """Log in as the user and create papers + models via the real routes.
    Returns the resulting model license_type values, for the summary report."""
    client.post("/auth/login", data={"email": email, "password": password}, follow_redirects=True)

    with client.application.app_context():
        user = User.query.filter(func.lower(User.email) == email.lower()).one()
        user_id = user.id
        existing_count = Paper.query.filter_by(user_id=user_id).count()

    license_types: list[str] = []
    if existing_count >= papers_per_user:
        with client.application.app_context():
            for paper in Paper.query.filter_by(user_id=user_id).all():
                for model in paper.models:
                    license_types.append(model.license_type)
        client.post("/auth/logout")
        return license_types

    for i in range(papers_per_user):
        spec = SAMPLE_PAPERS[(pool_offset + i) % len(SAMPLE_PAPERS)]
        visibility = "public" if i == 0 else "private"
        client.post(
            "/papers/new",
            data={
                "title": spec["title"],
                "authors": display_name,
                "year": "2025",
                "field": spec["field"],
                "abstract": spec["abstract"],
                "visibility": visibility,
            },
            follow_redirects=True,
        )
        with client.application.app_context():
            paper = (
                Paper.query.filter_by(user_id=user_id, title=spec["title"])
                .order_by(Paper.id.desc())
                .first()
            )
            paper_id, slug = paper.id, paper.slug

        client.post(
            f"/papers/{slug}/upload-model",
            data={
                "file": (io.BytesIO(DEMO_STL), f"demo-{pool_offset + i}.stl"),
                "compliance_confirm": "yes",
                "source_unit": "cm",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        with client.application.app_context():
            model = Model3D.query.filter_by(paper_id=paper_id).first()
            if model is not None:
                license_types.append(model.license_type)

    client.post("/auth/logout")
    return license_types


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--institution", help="Institution id or exact name. Auto-picked if only one exists.")
    parser.add_argument("--members", type=int, default=4, help="Number of demo member accounts (default 4).")
    parser.add_argument("--papers-per-user", type=int, default=2, help="Publications per demo user (default 2).")
    parser.add_argument("--password", default="Demo1234!", help="Shared password for all demo accounts.")
    parser.add_argument(
        "--domain",
        help="Override email domain for demo accounts (default: institution's first allowed domain, else example.com).",
    )
    args = parser.parse_args()

    app = create_app({"WTF_CSRF_ENABLED": False, "RATELIMIT_ENABLED": False})
    institution_id = resolve_institution_id(app, args.institution)

    with app.app_context():
        institution = db.session.get(Institution, institution_id)
        domain = args.domain or (institution.domain_list()[0] if institution.email_domains else "example.com")
        institution_name = institution.name
        institution_slug = institution.slug

        accounts = [("admin", f"institution.admin@{domain}", "Institution Admin")]
        for i in range(1, args.members + 1):
            accounts.append(("member", f"member{i}@{domain}", f"Demo Member {i}"))

        report_rows = []
        for role, email, display_name in accounts:
            user_id, created = ensure_user(email, display_name, args.password)
            membership_note = ensure_membership(user_id, institution_id, role="admin" if role == "admin" else "member")
            report_rows.append(
                {
                    "role": "Institution admin" if role == "admin" else "Member",
                    "email": email,
                    "display_name": display_name,
                    "user_id": user_id,
                    "created": created,
                    "membership": membership_note,
                }
            )

    client = app.test_client()
    for index, (row, (_role, email, display_name)) in enumerate(zip(report_rows, accounts)):
        if row["membership"].startswith("skipped"):
            row["license_types"] = []
            continue
        row["license_types"] = seed_papers_for_user(
            client, email, args.password, display_name, args.papers_per_user, pool_offset=index
        )

    try:
        with app.app_context():
            admin_dashboard_url = public_url("admin_user_detail", user_id=report_rows[0]["user_id"])
            panel_url = public_url("institution.overview")
            showcase_url = public_url("institution_showcase", slug=institution_slug) if institution_slug else None
    except Exception:
        admin_dashboard_url = f"/admin/users/{report_rows[0]['user_id']}"
        panel_url = "/institution/"
        showcase_url = f"/i/{institution_slug}" if institution_slug else None

    print()
    print(f"Institution: {institution_name} (id={institution_id}, slug={institution_slug or '—'})")
    print(f"Shared password for all demo accounts: {args.password}")
    print()
    print(f"{'Role':<18} {'Email':<32} {'Status':<28} Models (license)")
    print("-" * 100)
    for row in report_rows:
        status = "created" if row["created"] else "reused"
        status += f", {row['membership']}"
        licenses = ", ".join(row["license_types"]) or "—"
        print(f"{row['role']:<18} {row['email']:<32} {status:<28} {licenses}")

    print()
    print("View dashboards:")
    print(f"  - As platform admin (no login needed): {admin_dashboard_url}")
    print("    (same pattern, /admin/users/<id>, for every user id printed above — click \"Dashboard\" from there)")
    print(f"  - Institution panel (log in as institution.admin@{domain}): {panel_url}")
    if showcase_url:
        print(f"  - Public showcase (no login): {showcase_url}")
    else:
        print("  - Public showcase: institution has no slug yet — save its contract once in /admin/institutions to generate one.")
    print()
    print("Log in as any demo account with its email + the shared password above to see its own /dashboard.")


if __name__ == "__main__":
    main()
