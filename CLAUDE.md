# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Behavioral Guidelines

Karpathy's coding guardrails (github.com/multica-ai/andrej-karpathy-skills), adapted to
this repo. They bias toward caution over speed; use judgment on trivial tasks. The
project-specific rules in the rest of this file always take precedence.

### 1. Think Before Coding
State assumptions explicitly; if uncertain, ask. Surface tradeoffs and simpler
alternatives instead of silently picking one. For anything touching auth, payments/
webhooks, the conversion pipeline, licensing/QR, or compliance, confirm intent first —
these carry non-obvious invariants documented below.

### 2. Simplicity First
Write the minimum code that solves the task; nothing speculative (no unrequested
features, abstractions, or config). Reuse what exists before adding new code:
`url_helpers.public_url()`, `licensing.py` helpers (`model_access_status`,
`apply_model_license_defaults`), `services/r2_mirror`, the existing converters. If a
senior engineer would call it overcomplicated, rewrite it smaller.

### 3. Surgical Changes
Every changed line should trace to the request. Don't refactor or restyle adjacent
code, and match the surrounding style. NEVER redesign screens into a different visual
system — preserve the AcademicAR/Ventriloc language in `DESIGN.md` — and don't break
working flows (auth, publication management, PDF/model upload, processing states,
public viewer, QR pages, screenshot capture, consent). Remove only the orphans YOUR
change creates; flag pre-existing dead code rather than deleting it.

### 4. Goal-Driven Execution
Turn tasks into verifiable goals and loop until met. For a bugfix, first write a
regression test that reproduces it, then make it pass. Keep CPU/RAM-heavy work in the
worker, never inline in a web request. Before claiming work is done, run the
verification commands and read the output — evidence before claims:
- `python -m pytest tests -p no:cacheprovider`
- `python -m py_compile app.py auth.py models.py config.py licensing.py worker.py converters/*.py`

## Current Production Boundary

- Web requests must enqueue `ConversionJob` rows only; production conversion work belongs to `worker.py`, not the Flask web process.
- Upload rate limiting uses `Flask-Limiter`. In Railway/production, point `RATELIMIT_STORAGE_URI` at `REDIS_URL`; local dev and tests can use `memory://`.
- The model upload surface now accepts GLB, STL, OBJ, and FBX. OBJ/FBX conversion is handled by external converter wrappers.
- GLB output is optimized via `gltf-transform` (Draco geometry compression + webp textures) in `finalize_converted_glb()`. The optimizer is best-effort: if `gltf-transform` is unavailable the original GLB passes through unchanged.
- `fbx2gltf` is pinned to 0.9.7-p1 (the project is effectively unmaintained upstream). Do not upgrade without testing.

## Quick Commands

**Setup & Run**
```bash
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
npm install  # Node converter tools (obj2gltf, fbx2gltf, gltf-transform)

# Development server
python app.py

# Or use the local server runner
python run_local_server.py
```

**Testing**
```bash
# Run all tests
python -m pytest tests -p no:cacheprovider

# Single test file
python -m pytest tests/test_auth.py -p no:cacheprovider

# Single test
python -m pytest tests/test_auth.py::test_register -p no:cacheprovider
```

**Validation**
```bash
# Syntax check
python -m py_compile app.py auth.py models.py config.py licensing.py worker.py converters/base_converter.py converters/stl_converter.py converters/external_converter.py
```

**Database**
```bash
# Create migration (after model changes)
flask db migrate -m "description"

# Apply migrations
flask db upgrade

# Reset schema (development only)
flask db downgrade base
```

> **Migration architecture (deliberate design):** The initial migration
> (`669b2de1fcd7`) only creates indexes — it does **not** contain `CREATE TABLE`
> statements. The table schema is materialized at app startup via
> `db.create_all()`, after which `stamp_alembic_version_if_needed()` stamps the
> Alembic version to head. Fresh deployments therefore get their tables from
> `create_all()` (not from migration replay), then subsequent incremental
> migrations apply column/index changes idempotently. Do **not** rewrite the
> initial migration to add `CREATE TABLE` statements — doing so risks breaking
> the existing stamp logic and production schema.

## Architecture Overview

**Core Stack**: Flask (Python 3.12) + SQLAlchemy ORM + Jinja2 templates + Tailwind CSS + PostgreSQL/SQLite

## Current State & Implemented Features

- **Model-based licensing** (`licensing.py`): per-model plan (free/academic/extended_archive/institutional) with `LicensePlan` dataclass, access start/expiry dates, storage limits. Licensing is per-model ONLY — there is no account-level user plan and papers never expire (both were removed; do not reintroduce).
- **Institutional (B2B) module** (`institutions.py`, `institution_panel.py`): Institution contracts with quotas (model count + storage), invite-link membership (optional edu email-domain restriction, one institution per user), quota-funded "institutional" licensing on member uploads with expiry bound to `contract_ends_at`, an institution-admin panel at `/institution`, a public showcase at `/i/<slug>`, "Supported by" viewer attribution, offline contract payments (`Payment.institution_id`), and worker-driven monthly usage reports + 30-day renewal reminders.
- **Stable QR resolver** (`/m/<public_id>`): QRLink model maps public_id to model_id. QR codes survive replacements, upgrades, and color changes.
- **Model versioning**: ModelVersion table tracks replacement history per model. Replace-model flow preserves model ID, QR public ID, and resolver URL.
- **Converter pipeline**: GLB direct upload + STL/OBJ/FBX to GLB conversion via STLConverter (trimesh) and ExternalConverter (Node CLI wrappers). USDZ companion generated for iOS AR.
- **Background worker** (`worker.py`): polls ConversionJob rows. Production web processes only enqueue work.
- **Railway deployment**: web + worker in single container via `railway.json`, PostgreSQL, Redis for rate limits, persistent volume for files.

### Non-Negotiable Preservation Rules

- Preserve the existing AcademicAR / Ventriloc design language in `DESIGN.md`: white canvas, graphite text, thin borders, restrained gray surfaces, and sparing sunset-orange accents.
- Do not redesign screens into a different visual system while implementing infrastructure or product changes.
- Do not break current working flows unless the active task explicitly replaces them: auth, publication management, optional PDF upload, STL/GLB upload, model processing states, public viewer, QR pages, screenshot capture, and upload consent.
- Keep compliance confirmation mandatory for model upload and replacement.
- Public QR and viewer access must fail gracefully; avoid dead links when an upgrade/renew/unavailable state can be shown.

### Data Flow

1. **User Authentication** (`auth.py`, `models.User`)
   - Email/password or Google OAuth via Authlib
   - Session managed by Flask-Login
   - User can own multiple Papers

2. **Publication Creation** (Paper model)
   - User creates a Paper (thesis/article) with metadata (title, authors, year, DOI, PMID, abstract)
   - Papers carry no plan/package and never expire; access windows live on each
     model's license (`Model3D.license_type` + `access_expires_at`, checked at
     view time via `licensing.model_access_status`)
   - Optional PDF upload (user-only access)

3. **Model Upload & Conversion** (Model3D model + `converters/`)
   - User uploads GLB, STL, OBJ, or FBX file to a Paper
   - **STLConverter** validates and converts STL → GLB (via `trimesh` + `pygltflib`)
   - Files stored locally in `converted/`, `uploads/`, `qr_codes/`, `pdfs/` (ephemeral; move to S3/R2 for production)
   - Model gets UUID, QR code generated with `qrcode` package

4. **Public Viewing** (`/view/<model_id>`)
   - No login required
   - Checks the model's access window; expired models render the graceful
     `model_access_unavailable.html` state (HTTP 410), never a dead link
   - Serves GLB from `/files/<model_id>/model.glb`
   - Google `<model-viewer>` renders 3D + AR
   - Screenshot capture via `.toDataURL()` from model-viewer

5. **Compliance & Consent**
   - Upload requires explicit checkbox: anonymization + rights + ethics responsibility confirmed
   - Stored in `Model3D.anonymization_confirmed`, `rights_confirmed`, `ethics_responsibility_confirmed`, `consent_ip`, `consent_confirmed_at`
   - Must stay mandatory; compliance is core, not optional

### Key Classes & Relationships

- **User** → owns many **Papers** (cascade delete)
- **Paper** → owns many **Model3D** (cascade delete)
- **Model3D** → single GLB file + QR code + consent audit fields (+ optional funding `institution_id`)
- **Institution** → **InstitutionMember** rows (one institution per user; role member/admin) + **InstitutionInvite** join tokens; funds members' models within contract quota
- **Payment** → order/invoice record for per-model checkouts and manual institution contract payments
- **AuditLog** → optional, for compliance logging

### Important Implementation Details

- **URL generation**: `url_helpers.public_url()` builds public link URLs
- **Rate limiting**: Upload limits use `Flask-Limiter`; production should point `RATELIMIT_STORAGE_URI` at `REDIS_URL`, local dev can use `memory://`
- **Background worker**: `ConversionJob` rows are picked up by `worker.py`; production web containers must not run conversion inline
- **Slug generation**: `slugify()` creates unique Paper slugs (indexed for fast lookup)
- **CSRF protection**: Flask-WTF on all forms
- **Expiration check**: Happens at request time in routes like `/view/<model_id>` (no background cleanup job yet)
- **File security**: `secure_filename()` on all uploads; `send_from_directory()` for serving static files

### Configuration

Environment variables in `.env` (see `.env.example`):
- `APP_ENV`: `development` (default), `pilot`, or `production`
- `SECRET_KEY`: Must be set for non-dev modes
- `DATABASE_URL`: PostgreSQL connection string (optional; defaults to SQLite)
- `SITE_URL`: Public domain for QR codes and links
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`: OAuth setup

## Common Tasks

**Add a new model field**
1. Edit `models.py` (add column to a `db.Model` class)
2. Run `flask db migrate`
3. Review the migration file (check it looks correct)
4. Run `flask db upgrade`

**Change conversion logic**
- Edit `converters/stl_converter.py` (STLConverter class)
- Mesh validation, color handling, and trimesh configuration live here
- Tests in `tests/test_converters.py`

**Add a new route**
- Register in `app.py` or create a Blueprint in a separate file
- Use `@app.route()`, `@login_required`, `@csrf.exempt` (if needed) decorators
- Return `render_template()` or `jsonify()`

**Template changes**
- Templates in `templates/` (Jinja2)
- Static CSS in `static/css/style.css` (custom) + Tailwind CDN
- Use `{{ public_url(model.id) }}` to generate public viewer links in templates

## Deployment Notes

**Railway**
- Uses `Procfile` and `railway.json`
- Must set `SECRET_KEY` in environment
- Sets `APP_ENV=production`
- File storage is ephemeral; data persists in PostgreSQL only
- For production, move files to S3, Cloudflare R2, or Supabase Storage

**File Storage**
- Currently writes to local directories (`uploads/`, `converted/`, `qr_codes/`, `pdfs/`, `blog_images/`, `institution_logos/`)
- For production: implement an S3 backend in converters and file-serving routes
- Consider adding cleanup for files of models whose access window expired

## Testing Strategy

- Unit tests for converters (STL validation, GLB validation, format checks)
- Integration tests for auth (register, login, logout, Google callback)
- Functional tests for upload/model flow (model creation, expiration, public access)
- Use `tests_runtime/` for test artifacts (pytest temp files)
- `.pytest_cache` is gitignored on Windows due to file locking; expected behavior
