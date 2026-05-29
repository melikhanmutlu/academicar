# AcademicAR Project Guidance

This file captures the current implementation guardrails for the next MVP phase. Use it together with `DESIGN.md`, `PROJECT_DESCRIPTION.md`, and `MVP_IMPLEMENTATION_PLAN.md`.

## Active Source of Truth

`MVP_IMPLEMENTATION_PLAN.md` is the working checklist for the model-based licensing and infrastructure MVP.

When a task is completed, update the checklist in that file. Keep completed items visible with checked boxes, and use strikethrough notes only when it helps show what was replaced or intentionally deferred.

## Product Direction

- Pricing is model-based, not subscription-based.
- Users create an account first, then create a publication/project, then attach one or more models.
- Each model can start as Free Access and later be upgraded, or a purchased license credit can be applied before/while uploading.
- A model upgrade must preserve the same model identity, public resolver URL, viewer URL behavior, and QR destination.
- Free Access, Academic, and Extended Archive plans should map cleanly to model access duration, storage limit, and viewer capabilities.
- Institutional licensing remains a sales-led path for labs, research groups, and universities.

## Technical Direction

- Start on Railway for the MVP: web service, worker service, PostgreSQL-backed job state, Redis-backed rate limits, and persistent storage/volume where practical.
- Keep the architecture portable with Docker so the system can later move to a VPS or mixed infrastructure without rewriting the app.
- Introduce a storage abstraction before deeply coupling file handling to a single provider.
- Split long-running model processing into background worker jobs through `ConversionJob` and `worker.py`; web requests must enqueue jobs only and must not run conversion in-process.
- Use Redis-backed `Flask-Limiter` for upload rate limits in Railway/production. Local dev and tests can use `memory://`, but production should point `RATELIMIT_STORAGE_URI` at `REDIS_URL`.
- Preserve source and GLB history with `ModelVersion` so replacements, failures, and future storage migrations stay auditable.
- Support direct GLB upload and STL/OBJ/FBX conversion to AR-ready GLB.
- Adapt only the conversion structure needed from `C:\Users\syste\Desktop\Web & Dev\Projeler\web_ar-main`; do not copy unrelated application logic.

## Design Preservation

- Preserve the AcademicAR / Ventriloc style: white canvas, graphite text, thin borders, quiet gray surfaces, and limited sunset-orange emphasis.
- Do not redesign screens into a different visual system while implementing pricing, upload, conversion, or infrastructure work.
- Avoid decorative UI that conflicts with the restrained product language.
- Dashboard upload affordances can become more visible, but they should remain precise, compact, and aligned with the current interface.

## Working Flow Preservation

Do not break these existing flows unless the active task explicitly replaces them:

- Email/password authentication
- Google sign-up/login work
- Publication creation and management
- Optional PDF upload
- Model upload and processing states
- Upload compliance consent
- Public viewer pages
- AR access
- QR generation and QR pages
- Screenshot export
- Existing model color/material behavior

## Link and QR Rules

- QR codes should point to a managed resolver URL, not directly to a raw model file.
- Resolver URLs must remain stable across license upgrades, model replacements, color updates, file conversions, and storage migrations.
- If access is expired or unavailable, show a controlled viewer state instead of allowing a dead link.
- A model replacement may update the underlying GLB asset but must not change the public model identity.

## Implementation Style

- Keep changes small enough to verify.
- Prefer existing Flask, SQLAlchemy, Jinja, Tailwind, and `model-viewer` patterns already used in the project.
- Keep database schema changes deliberate and documented; the current MVP uses startup schema guards, and a formal migration tool should be introduced before production data grows.
- Add focused tests for licensing, resolver behavior, upload limits, conversion jobs, and replacement flows as those areas are implemented.
