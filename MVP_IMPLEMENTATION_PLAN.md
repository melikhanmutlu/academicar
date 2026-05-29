# AcademicAR MVP Implementation Plan

This is the working implementation checklist for the next AcademicAR MVP phase.
As work is completed, mark items with `[x]` and optionally wrap completed notes in `~~strikethrough~~` so progress stays visible.

## Guiding Principles

- Preserve the current AcademicAR / Ventriloc design language from `DESIGN.md`: clean white canvas, thin borders, restrained graphite/gray palette, and limited sunset-orange accents.
- Do not break working flows while adding the new architecture. Existing auth, publication creation, model upload, viewer, QR, screenshot, PDF, and consent flows must continue to work unless a task explicitly replaces them.
- Keep model IDs, QR resolver URLs, and viewer links stable through license upgrades, model replacement, color updates, converter changes, and future storage migration.
- Treat QR codes as long-lived academic access records, not direct file links.
- Implement in small, testable slices. Every slice should leave the app usable.

## 1. Product Model and Pricing

- [x] Convert the product model from user/publication package logic toward model-based licensing.
- [x] Define model license types:
  - [x] `free`: `$0`, 3-day active AR and QR access.
  - [x] `academic`: `$9.90 / model`, 3-year active access.
  - [x] `extended_archive`: `$24.90 / model`, 10-year renewable archive access.
  - [x] `institutional`: offer-based B2B licensing.
- [x] Update pricing copy from `per publication` to `per model`.
- [x] Keep Extended Archive as 10-year renewable access, not lifetime access.
- [x] Make expired Free models fall back to an upgrade/renew screen instead of a dead QR/link.
- [x] Keep the account free; paid value belongs to model access records.

## 2. Managed QR Resolver

- [x] Add a managed QR resolver route:
  - [x] Public format: `https://ar.academicar.com/m/<public_id>`.
  - [x] Local/dev equivalent route can live in the Flask app first.
- [x] Ensure QR codes point to resolver URLs, not direct `/view/<model_id>` or file URLs.
- [x] Add a QR record concept:
  - [x] `public_id`
  - [x] `model_id`
  - [x] `status`
  - [x] `target_type`
  - [x] `created_at`
  - [x] `last_resolved_at`
- [x] Resolver behavior:
  - [x] Active model opens viewer/AR page.
  - [x] Expired model opens upgrade/renew screen.
  - [x] Missing/unavailable model opens a controlled unavailable state.
  - [x] Future storage/provider changes do not change the public QR URL.

## 3. Railway-First Infrastructure

- [x] Start with a Railway-first architecture:
  - [x] Railway Web Service: Flask + Gunicorn.
  - [x] Railway Worker Service: conversion jobs.
  - [x] Railway PostgreSQL.
  - [x] DB-backed queue for the MVP, with Redis still possible later if job volume requires it.
  - [x] Railway persistent volume for the initial MVP storage.
- [x] Keep the architecture portable so storage and workers can move later without changing QR links.
- [x] Add environment variables:
  - [x] `DATABASE_URL`
  - [x] `REDIS_URL`
  - [x] `SITE_URL`
  - [x] `APP_BASE_URL`
  - [x] `QR_BASE_URL`
  - [x] `STORAGE_PROVIDER=railway_volume`
  - [x] `UPLOAD_FOLDER`
  - [x] `CONVERTED_FOLDER`
  - [x] `QR_FOLDER`
  - [x] `PDF_FOLDER`
  - [x] `GOOGLE_CLIENT_ID`
  - [x] `GOOGLE_CLIENT_SECRET`
- [x] Plan Railway volume folders:
  - [x] `originals/`
  - [x] `converted/`
  - [x] `qr/`
  - [x] `pdfs/`
  - [x] `previews/`
  - [x] `temp/`
- [x] Use Cloudflare DNS for:
  - [x] `academicar.com`
  - [x] `app.academicar.com`
  - [x] `ar.academicar.com`

## 4. Storage Abstraction

- [x] Add a `StorageService` abstraction before relying on any long-term storage choice.
- [x] First provider: `railway_volume`.
- [x] Future providers should be possible without changing model URLs:
  - [x] `cloudflare_r2`
  - [x] `s3_compatible`
  - [x] `local_vps`
- [x] Store provider-aware file references:
  - [x] `storage_provider`
  - [x] `storage_key`
  - [x] `file_role`
- [x] Storage API should support:
  - [x] `save_file`
  - [x] `read_file`
  - [x] `delete_file`
  - [x] `exists`
  - [x] `get_serving_url`
  - [x] `migrate_file`
- [x] Do not encode Railway-specific paths into QR or public viewer URLs.

## 5. Web, Worker, and Queue Split

- [x] Move conversion work out of web requests.
- [ ] Add a queue-backed job flow for upload, replacement, color/material update, previews, and AR companion generation.
  - [x] Upload conversion jobs.
  - [x] Replacement conversion jobs.
  - [ ] Color/material update jobs can reuse the same conversion runner.
  - [ ] Preview/video generation jobs.
  - [x] AR companion generation jobs.
- [x] Web service responsibilities:
  - [x] auth
  - [x] dashboard
  - [x] publications
  - [x] upload request intake
  - [x] license state
  - [x] QR resolver
  - [x] UI status
- [x] Worker responsibilities:
  - [x] STL -> GLB
  - [x] OBJ + MTL + textures -> GLB
  - [x] FBX -> GLB
  - [x] GLB validation/copy
  - [x] USDZ/AR companion generation
  - [ ] thumbnail/preview video generation
  - [ ] model color/material updates
  - [x] replace model file jobs
- [x] Worker failure must never break the last successful public model.

## 6. Converter Integration

- [x] Adapt converter infrastructure from `C:\Users\syste\Desktop\Web & Dev\Projeler\web_ar-main`.
- [x] Bring over and adapt, not blindly copy:
  - [x] `OBJConverter`
  - [x] `FBXConverter`
  - [x] required `BaseConverter` behavior
  - [x] `tools/FBX2glTF.exe`
  - [x] `tools/FBX2glTF`
  - [x] `tools/blender_usdz_export.py`
- [x] Keep AcademicAR's current `STLConverter` unless a verified replacement is safer.
- [x] Add converter registry:
  - [x] `.stl` -> `STLConverter`
  - [x] `.obj` -> `OBJConverter`
  - [x] `.fbx` -> `FBXConverter`
  - [x] `.glb` -> direct validate/copy
- [x] Add Node dependency management with `package.json`; do not copy `node_modules`.
- [x] Required Node tools:
  - [x] `obj2gltf`
  - [x] `fbx2gltf`
  - [x] `three` if needed by preview/conversion tooling.
- [x] Docker image must include:
  - [x] Python dependencies
  - [x] Node.js
  - [x] `obj2gltf`
  - [x] FBX2glTF tools
  - [x] Blender CLI

## 7. Texture and Material Rules

- [x] OBJ upload must support optional companion files:
  - [x] `.mtl`
  - [x] `.png`
  - [x] `.jpg`
  - [x] `.jpeg`
  - [x] `.webp`
- [x] OBJ conversion:
  - [x] Preserve MTL and texture files in the temp upload directory.
  - [x] Use `obj2gltf --binary --checkTransparency`.
  - [x] Preserve textures/materials by default.
  - [x] Apply `material_color` only when no texture/material should be preserved.
- [x] FBX conversion:
  - [x] Use FBX2glTF.
  - [x] Preserve embedded/external textures where possible.
  - [x] Embed or normalize textures for model-viewer compatibility if needed.
  - [x] Preserve transparency, double-sided materials, and PBR visual quality.
- [x] Default behavior: preserve textures/materials over applying solid color.
- [x] If solid color would overwrite textures, require a separate explicit choice.

## 8. Upload and Replace Flows

- [x] Accept model formats:
  - [x] `.glb`
  - [x] `.stl`
  - [x] `.obj`
  - [x] `.fbx`
- [x] Update upload inputs to accept `.glb,.stl,.obj,.fbx`.
- [x] Add optional MTL/texture fields for OBJ uploads.
- [x] Add drag-and-drop upload on:
  - [x] publication creation
  - [x] publication detail
  - [x] dashboard quick upload area
- [x] Allow first model upload during publication creation.
- [x] Mark PDF upload as `Optional PDF upload`.
- [x] Support multi-upload; each file creates its own model record and license state.
- [x] After license upgrade:
  - [x] file size limit increases
  - [x] `Replace model file` becomes available under the new limit
  - [x] model ID remains unchanged
  - [x] QR/public ID remains unchanged
  - [x] resolver URL remains unchanged
- [x] Replace model file behavior:
  - [x] Save new source under a versioned path.
  - [x] Convert to temporary GLB first.
  - [x] Swap into canonical `model.glb` only after successful conversion.
  - [x] Preserve the previous working GLB on failure.
  - [x] Increment `version_number`.
  - [x] Update `last_replaced_at`.
- [x] Replacement states:
  - [x] `ready`
  - [x] `replacement_processing`
  - [x] `replacement_failed`

## 9. Model Appearance Updates

- [x] Allow color/material updates after upload.
- [x] Keep model ID, QR, and resolver URL unchanged.
- [x] Add UI:
  - [x] color picker
  - [x] text command field, e.g. `make it light gray`
- [x] Add endpoint:
  - [x] `POST /models/<model_id>/appearance`
- [x] Preserve textures by default for OBJ/FBX models.
- [x] Failed appearance updates must preserve the previous working GLB.

## 10. Database Changes

- [x] Add or migrate model-level license fields on `Model3D`:
  - [x] `license_type`
  - [x] `license_status`
  - [x] `license_started_at`
  - [x] `license_expires_at`
  - [x] `max_file_size_bytes`
  - [x] `material_color`
  - [x] `source_format`
  - [x] `original_source_path`
  - [x] `current_source_path`
  - [x] `glb_path`
  - [x] `storage_provider`
  - [x] `storage_key`
  - [x] `version_number`
  - [x] `last_replaced_at`
  - [x] `replacement_status`
  - [x] `replacement_error`
- [x] Add `QRRecord`.
- [x] Consider `ModelVersion` for replace history:
  - [x] `model_id`
  - [x] `version_number`
  - [x] `source_path`
  - [x] `glb_path`
  - [x] `source_format`
  - [x] `file_size`
  - [x] `material_color`
  - [x] `storage_provider`
  - [x] `storage_key`
  - [x] `status`
  - [x] `created_at`
- [x] Add `ConversionJob` or equivalent persistent queue-backed job state.
- [x] Keep backward compatibility for existing local models and links.

## 11. UI Changes

- [x] Landing/pricing:
  - [x] implement Free / Academic / Extended Archive / Institutional structure
  - [x] change `per publication` copy to `per model`
  - [x] keep existing design language
- [x] Publication create:
  - [x] metadata
  - [x] optional PDF
  - [x] first model upload
  - [x] drag-drop
  - [x] Free default license
- [x] Publication detail:
  - [x] prominent upload area
  - [x] multiple models per publication
  - [x] multi-file batch upload from one submission
  - [x] model cards with license, source format, version, file limit
  - [x] `Upgrade license`
  - [x] `Replace model file`
  - [x] `Edit color`
  - [x] typed color command for GLB material updates
  - [x] `Copy resolver link`
  - [x] `View QR`
- [x] Dashboard:
  - [x] more visible upload CTA
  - [x] empty-state demo publication/model cards
  - [x] quick `Add model` action per publication
- [x] Viewer:
  - [x] visible QR tool or bottom-right QR area
  - [x] demo mitochondria QR points to its resolver/AR target
  - [x] expired Free models show upgrade/renew state

## 12. Google Auth and Demo Experience

- [x] Activate Google signup/login when env vars are configured.
- [x] If Google env vars are missing, show a disabled/explanatory Google button instead of a broken flow.
- [x] Add UI-level demo dashboard examples for new users:
  - [x] demo publication
  - [x] demo model
  - [x] demo QR
  - [x] upload CTA
- [x] Avoid mandatory seed data in user databases for demos unless explicitly needed.

## 13. Deployment

- [x] Add Dockerfile for Railway and future VPS portability.
- [x] Web command:
  - [x] `gunicorn app:app`
- [x] Worker command:
  - [x] `python worker.py`
- [x] Ensure Docker image can run both web and worker commands.
- [x] Add/verify Railway configuration for multiple services.
- [x] Keep custom domain assumptions:
  - [x] `academicar.com`
  - [x] `app.academicar.com`
  - [x] `ar.academicar.com`
- [x] Do not couple public URLs to Railway-generated domains.

## 14. Test Plan

- [x] GLB direct upload renders in viewer/AR.
- [x] STL upload converts to GLB and renders in viewer/AR.
- [x] OBJ + MTL + texture upload preserves texture/material in GLB.
- [x] OBJ without textures can receive color/material.
- [x] FBX upload converts to GLB with materials/textures preserved where possible.
- [x] Missing FBX2glTF produces a clear user-facing error.
- [x] Free model upgrade to Academic increases file limit.
- [x] Replace after upgrade uses the same resolver link and QR.
- [x] Failed replace preserves previous working model.
- [x] Color update keeps link and QR stable.
- [x] Expired Free model opens upgrade/renew state.
- [x] Free Access expires 3 days after model upload while preserving model, QR, resolver URL, and upgrade path.
- [x] QR resolver handles active, expired, unavailable, and replacement states.
- [x] Google OAuth configured and unconfigured scenarios work.
- [x] Railway web and worker services run from the same Docker image with different commands.
- [x] Production web uploads only enqueue `ConversionJob`; conversion is not run by the web process.
- [x] Upload rate limiting uses `Flask-Limiter` with Redis-compatible storage instead of in-memory dictionaries.

## 15. Deferred or Explicitly Out of Scope for Now

- [ ] Secondary backup storage.
- [ ] Detailed profitability/cost guardrails.
- [ ] Long-term object storage migration implementation.
- [ ] Dedicated VPS worker deployment.
- [ ] Full institutional billing automation.
