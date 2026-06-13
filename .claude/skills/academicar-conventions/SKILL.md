---
name: academicar-conventions
description: Engineering conventions and hard boundaries for the AcademicAR Flask app — use when changing backend code, the conversion pipeline, licensing/QR, payments, uploads, migrations, or deciding where work belongs (web vs worker). Encodes the production boundary and the rules in CLAUDE.md so changes don't break the current architecture.
---

# AcademicAR Engineering Conventions

Stack: Flask (Python 3.12) + SQLAlchemy + Jinja2 + Tailwind + PostgreSQL/SQLite.
`CLAUDE.md` is the fuller reference; this skill is the must-not-break list.

## Production boundary (most important)
- Web requests must only **enqueue `ConversionJob` rows**. CPU/RAM-heavy 3D
  conversion runs in `worker.py` (`run_next_conversion_job` /
  `process_model_upload_job`), never inline in a production web process.
- Tests / local dev may run inline via `TESTING` or `DEV_INLINE_JOBS`.

## Conversion pipeline (`converters/`)
- Accepts GLB, STL, OBJ, FBX. STL→GLB via `STLConverter` (trimesh/pygltflib);
  OBJ/FBX via `ExternalConverter` Node CLI wrappers (obj2gltf, fbx2gltf).
- GLB optimized in `finalize_converted_glb()` (gltf-transform: Draco + webp),
  best-effort — passes through unchanged if the tool is missing.
- `fbx2gltf` pinned to 0.9.7-p1; do not upgrade without testing.
- iOS USDZ companion generated via headless Blender (`convert_glb_to_usdz`,
  `tools/blender_usdz_export.py`); Draco-decompress first.

## Licensing, QR, versioning (`licensing.py`, models)
- Per-model plans (free / academic / extended_archive) with access start/expiry.
  Expiry is checked at request time (e.g. `/view/<id>`), no cleanup job.
- Stable QR resolver `/m/<public_id>` → `QRLink` → model; QR survives replacement,
  upgrade, color change. Replace-model preserves model id, public_id, resolver URL.
- Public QR/viewer must fail gracefully (upgrade/renew/unavailable), never dead links.

## Compliance (mandatory — never make optional)
- Model upload and replacement require the anonymization + rights + ethics
  confirmation checkbox; stored in `Model3D.*_confirmed` + `consent_ip` /
  `consent_confirmed_at`.

## Infra rules
- Rate limiting: Flask-Limiter; prod `RATELIMIT_STORAGE_URI` → `REDIS_URL`,
  local/tests `memory://`.
- File storage is ephemeral on Railway; durable copy goes to R2/S3 via
  `services/r2_mirror`. Persisted truth lives in PostgreSQL.
- CSRF (Flask-WTF) on all forms; `secure_filename()` on uploads;
  `send_from_directory()` for serving.

## Migrations (deliberate, do not "fix")
- Initial migration `669b2de1fcd7` creates **indexes only**, no `CREATE TABLE`.
  Tables are materialized at startup via `db.create_all()`, then
  `stamp_alembic_version_if_needed()` stamps to head. Do NOT add `CREATE TABLE`
  to the initial migration. New incremental migrations apply column/index changes
  idempotently (`if_not_exists=True`).

## Commands
- Tests: `python -m pytest tests -p no:cacheprovider`
- Syntax check: `python -m py_compile app.py auth.py models.py config.py licensing.py worker.py converters/*.py`
- Migrations: `flask db migrate -m "..."` → review → `flask db upgrade`

## Before you ship a change
1. Did heavy work stay in the worker, not the web request?
2. Did you keep compliance mandatory and QR/viewer failure-states graceful?
3. Run the test suite; add/extend a regression test for the behavior you changed.
4. Don't break working flows (auth, publication mgmt, PDF/model upload, processing
   states, public viewer, QR pages, screenshot capture, consent) unless the task
   explicitly replaces them.
