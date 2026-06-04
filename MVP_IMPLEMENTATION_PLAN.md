# MVP Implementation Plan

Working source of truth for the active implementation phase. Keep the checklist
current: tick items as they land and add short notes.

## Product Direction (unchanged)

- Model-based licensing (per-model plan ownership), not user/publication-level.
- Stable managed QR resolver URLs (`/m/<public_id>`).
- Railway-first deploy: web + worker + PostgreSQL + Redis + persistent volume.
- Converters: GLB direct upload; STL/OBJ/FBX → GLB.
- Replace/appearance flows preserve model id, QR public id, and resolver URL.

## v6 Audit Hardening (in progress)

### Tier 1 — Critical
- [x] Worker process supervision via `start.sh` (sync migrations, worker
      auto-restart, SIGTERM forwarding); `railway.json` calls it.
- [x] Conversion jobs: reclaim stale `processing` jobs, bounded by
      `max_attempts`; `attempts < max_attempts` guard on the pending query.
- [x] Email header injection: strip CR/LF from recipient + subject.
- [x] Index FK columns (`papers.user_id`, `models.paper_id`, `models.user_id`).
- [x] `ModelVersion` unique `(model_id, version_number)` + row-locked,
      MAX-based next-version computation on replace.
- [x] Migration `b7f2c1a9d4e3` ships the above to existing databases (guarded).

### Tier 2 — High
- [x] QR resolver falls back to legacy `public_id` lookup for orphaned links.
- [x] `paper_is_expired()` honors `expires_at` instead of always `False`.
- [x] Soft-delete consistency: `status` + `deleted_at` kept in sync; either
      signal counts as deleted.
- [x] `glb_path` nullable (row exists before its GLB is produced).
- [x] Per-IP rate limit on public viewer + QR resolver (`PUBLIC_VIEW_RATE_LIMIT`).
- [x] Worker purges papers soft-deleted past `DELETED_PAPER_GRACE_DAYS`
      (DB + files); web stays request-only.
- [x] Optional per-user total storage cap (`USER_TOTAL_STORAGE_BYTES`, off by default).

### Tier 3 — Medium
- [x] Standardized upload form field names (`file`/`companion_files`/
      `display_name`/`description`); removed backend dual-name fallbacks.
- [x] `url_for` instead of hardcoded JS endpoints.
- [x] Removed hardcoded developer path from `run_local_server.py`.
- [x] `requirements.txt` security bumps; `pytest` moved to `requirements-dev.txt`.
- [x] `services/` and `utils/` `__init__.py`.
- [x] Admin dashboard pagination (users/papers/models lists).
- [x] Status constants module (`constants.py`); adopted in the job + soft-delete
      paths (gradual adoption elsewhere — values are unchanged, so safe).
- [ ] CSP hardening (self-hosted Tailwind build to drop `unsafe-eval`) —
      **deferred dedicated task.** Requires adding a Node/PostCSS build to a
      Python-only repo and re-auditing every inline style/script; high risk to
      the non-negotiable design language. CSP stays enabled with documented
      CDN allowances until this is scheduled on its own.

### Tier 4 — Backlog / polish
- [x] Shared `ensure_utc()` helper to de-duplicate timezone handling.
- [x] Documented the always-`"model_based"` `package_type` column (kept to
      avoid a migration; still read as a display hint).
- [x] Open Graph / Twitter meta tags on public paper + viewer pages.
- [x] Avatar alt-text; decorative hero already `aria-hidden`.
- [x] Email verification on password registration (non-blocking banner +
      signed-token link; Google users auto-verified; legacy users grandfathered).
- [x] Backup retention/cleanup (`BACKUP_RETENTION_COUNT`).
- [x] `.dockerignore` / `.slugignore` to slim the production image.

## Non-Negotiable Preservation Rules

See `CLAUDE.md` and `DESIGN.md`. Preserve the AcademicAR / Ventriloc design
language; keep compliance confirmation mandatory; public QR/viewer access must
fail gracefully (upgrade/renew/unavailable states, never dead links).
