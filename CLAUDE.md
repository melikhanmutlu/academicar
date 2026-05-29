# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current Production Boundary

- Web requests must enqueue `ConversionJob` rows only; production conversion work belongs to `worker.py`, not the Flask web process.
- Upload rate limiting uses `Flask-Limiter`. In Railway/production, point `RATELIMIT_STORAGE_URI` at `REDIS_URL`; local dev and tests can use `memory://`.
- The model upload surface now accepts GLB, STL, OBJ, and FBX. OBJ/FBX conversion is handled by external converter wrappers.

## Quick Commands

**Setup & Run**
```bash
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt

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
python -m py_compile app.py auth.py models.py config.py converters/base_converter.py converters/stl_converter.py
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

## Architecture Overview

**Core Stack**: Flask (Python 3.12) + SQLAlchemy ORM + Jinja2 templates + Tailwind CSS + PostgreSQL/SQLite

## Current MVP Direction

Use `MVP_IMPLEMENTATION_PLAN.md` as the working source of truth for the next implementation phase. Update its checklist as tasks are completed, and keep completed work visible by checking items off and optionally using Markdown strikethrough notes.

The next MVP direction is:

- Model-based licensing instead of user/publication-level plan ownership.
- Stable managed QR resolver URLs (`/m/<public_id>`) instead of QR codes pointing directly at model files or volatile viewer paths.
- Railway-first deployment with web, worker, PostgreSQL, Redis, and persistent volume, while keeping storage/provider logic portable.
- Converter expansion for GLB direct upload and STL/OBJ/FBX to GLB conversion, adapting the working converter approach from `C:\Users\syste\Desktop\Web & Dev\Projeler\web_ar-main`.
- Replace-model and appearance-update flows must preserve the model ID, QR public ID, and resolver URL.

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
   - Two package types: `temporary` (3-day free) or `academic` (3-year paid)
   - Expires based on `expires_at`; checked at view time, not cleaned up proactively
   - Optional PDF upload (user-only access)

3. **Model Upload & Conversion** (Model3D model + `converters/`)
   - User uploads GLB, STL, OBJ, or FBX file to a Paper
   - **STLConverter** validates and converts STL → GLB (via `trimesh` + `pygltflib`)
   - Files stored locally in `converted/`, `uploads/`, `qr_codes/`, `pdfs/` (ephemeral; move to S3/R2 for production)
   - Model gets UUID, QR code generated with `qrcode` package

4. **Public Viewing** (`/view/<model_id>`)
   - No login required
   - Checks expiration; returns 404 if expired
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
- **Model3D** → single GLB file + QR code + consent audit fields
- **Payment** → optional record for future paid package handling
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
- Currently writes to local directories (`uploads/`, `converted/`, `qr_codes/`, `pdfs/`)
- For production: implement an S3 backend in converters and file-serving routes
- Consider adding cleanup for expired temporary publications

## Testing Strategy

- Unit tests for converters (STL validation, GLB validation, format checks)
- Integration tests for auth (register, login, logout, Google callback)
- Functional tests for upload/model flow (model creation, expiration, public access)
- Use `tests_runtime/` for test artifacts (pytest temp files)
- `.pytest_cache` is gitignored on Windows due to file locking; expected behavior
