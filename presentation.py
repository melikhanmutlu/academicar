"""Presenting 3D models at a talk without switching windows or needing the internet.

Two routes, both for project editors:

* PowerPoint-ready GLB (``/models/<id>/powerpoint``): the worker (job
  ``powerpoint_glb``) writes ``converted/<id>/powerpoint.glb`` with
  ``converters.powerpoint_export``; PowerPoint embeds it via Insert > 3D Models,
  so the deck works offline on any congress computer. ``powerpoint.json`` next
  to it records which GLB / default layer view it was built from.
* Presenter mode (``/p/<slug>/materials/<id>/present``): a material's PDF pages
  as full-screen slides with live 3D model slides between them
  (``ProjectAttachment.model_slides``). A service worker (``/present-sw.js``)
  keeps the page, the PDF and the models so it also runs offline.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from flask import (
    Blueprint, abort, current_app, jsonify, render_template, request, send_from_directory, url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from collaborators import can_edit_project
from converters.powerpoint_export import POWERPOINT_MAX_TRIANGLES, build_powerpoint_glb
from layer_editor import default_ar_materials
from licensing import model_access_status, project_supports_feature
from models import ConversionJob, Model3D, Paper, ProjectAttachment, db
from services.r2_mirror import ensure_local
from utils.security import require_model_editor

logger = logging.getLogger(__name__)

presentation_bp = Blueprint("presentation", __name__)

POWERPOINT_JOB = "powerpoint_glb"
MAX_MODEL_SLIDES = 40


# --------------------------------------------------------------------------
# PowerPoint-ready GLB
# --------------------------------------------------------------------------

def powerpoint_paths(model: Model3D) -> tuple[str, str]:
    folder = os.path.join(current_app.config["CONVERTED_FOLDER"], model.id)
    return os.path.join(folder, "powerpoint.glb"), os.path.join(folder, "powerpoint.json")


def powerpoint_signature(model: Model3D) -> str:
    """What the PowerPoint file depends on: the GLB (version, size, mtime, so a
    recolour or replacement counts) and the saved default layer view."""
    hidden, faded = default_ar_materials(model)
    stat = None
    if model.glb_path and os.path.exists(model.glb_path):
        st = os.stat(model.glb_path)
        stat = [st.st_size, st.st_mtime_ns]
    return json.dumps(
        [model.version or 1, stat, sorted(hidden), sorted(faded.items()), POWERPOINT_MAX_TRIANGLES],
        separators=(",", ":"),
    )


def _built_signature(sidecar: str) -> str | None:
    try:
        with open(sidecar, encoding="utf-8") as fh:
            return json.load(fh).get("signature")
    except (OSError, ValueError, AttributeError):
        return None


def _latest_job(model: Model3D) -> ConversionJob | None:
    return (
        ConversionJob.query.filter_by(model_id=model.id, job_type=POWERPOINT_JOB)
        .order_by(ConversionJob.id.desc())
        .first()
    )


def powerpoint_state(model: Model3D) -> dict:
    """{"status": ready | preparing | failed | missing, ...} for the button."""
    glb, sidecar = powerpoint_paths(model)
    signature = powerpoint_signature(model)
    if os.path.exists(glb) and _built_signature(sidecar) == signature:
        return {
            "status": "ready",
            "download_url": url_for("presentation.powerpoint_download", model_id=model.id),
            "size_bytes": os.path.getsize(glb),
        }
    job = _latest_job(model)
    if job is not None and job.status in {"pending", "processing"}:
        return {"status": "preparing"}
    if job is not None and job.status == "failed" and (job.payload or {}).get("signature") == signature:
        return {"status": "failed", "error": "The PowerPoint file could not be prepared for this model."}
    return {"status": "missing"}


def process_powerpoint_glb_job(app, *, model_id: str, job_id: int | None = None, signature: str | None = None) -> None:
    """Worker step for the ``powerpoint_glb`` job (optional: a failure is
    recorded on the job only, never on the model)."""
    with app.app_context():
        model = db.session.get(Model3D, model_id)
        job = db.session.get(ConversionJob, job_id) if job_id is not None else None
        if job is not None and job.status != "processing":
            job.status = "processing"
            job.started_at = datetime.now(UTC)
            job.attempts = (job.attempts or 0) + 1
            db.session.commit()
        error = None
        try:
            if model is None or not model.glb_path:
                error = "Model or its GLB path is missing."
            else:
                ensure_local(model.glb_path, f"converted/{model_id}/model.glb")
                if not os.path.exists(model.glb_path):
                    error = "The converted GLB file is missing."
                else:
                    glb, sidecar = powerpoint_paths(model)
                    current = powerpoint_signature(model)
                    hidden, faded = default_ar_materials(model)
                    if build_powerpoint_glb(model.glb_path, glb, hidden, faded):
                        with open(sidecar, "w", encoding="utf-8") as fh:
                            json.dump({"signature": current, "built_at": datetime.now(UTC).isoformat()}, fh)
                    else:
                        error = "The PowerPoint GLB could not be built."
                    if job is not None:
                        job.payload = {**(job.payload or {}), "signature": current}
        except Exception as exc:
            db.session.rollback()
            logger.exception("PowerPoint GLB job failed for model %s", model_id)
            error = f"{type(exc).__name__}: {exc}"[:500]
        if job is not None:
            job.status = "failed" if error else "completed"
            job.error = error
            job.finished_at = datetime.now(UTC)
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()


def _powerpoint_model_or_abort(model_id: str) -> Model3D:
    model = db.session.get(Model3D, model_id)
    if model is None:
        abort(404)
    if model_access_status(model) != "active":
        abort(409)
    return model


@presentation_bp.route("/models/<model_id>/powerpoint", methods=["GET", "POST"])
@login_required
@require_model_editor
def powerpoint_prepare(model_id):
    """GET: the PowerPoint file's state. POST: queue it when it is missing or stale."""
    model = _powerpoint_model_or_abort(model_id)
    state = powerpoint_state(model)
    if request.method == "POST" and state["status"] in {"missing", "failed"}:
        from app import enqueue_conversion_job, log_audit

        try:
            enqueue_conversion_job(
                current_app._get_current_object(), model=model, job_type=POWERPOINT_JOB,
                job_kwargs={"model_id": model.id, "signature": powerpoint_signature(model)},
            )
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not queue the PowerPoint GLB for model %s", model.id)
            return jsonify({"status": "failed", "error": "Could not start preparing the file. Try again."}), 503
        log_audit("model_powerpoint_requested", user_id=current_user.id, resource_id=model.id)
        state = powerpoint_state(model)
    return jsonify(state)


@presentation_bp.route("/models/<model_id>/powerpoint.glb")
@login_required
@require_model_editor
def powerpoint_download(model_id):
    model = _powerpoint_model_or_abort(model_id)
    if powerpoint_state(model)["status"] != "ready":
        abort(404)
    glb, _ = powerpoint_paths(model)
    base = os.path.splitext(model.display_name or model.original_filename or "model")[0]
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in base).strip() or "model"
    response = send_from_directory(
        os.path.dirname(glb), os.path.basename(glb), mimetype="model/gltf-binary",
        as_attachment=True, download_name=f"{safe[:80]} (PowerPoint).glb",
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


# --------------------------------------------------------------------------
# Presenter mode
# --------------------------------------------------------------------------

def _presentable_models(project: Paper) -> list[Model3D]:
    return [m for m in project.models if model_access_status(m) == "active"]


def clean_model_slides(raw, page_count: int | None, model_ids: set[str]) -> list[dict]:
    """Whitelist ``[{"after": int, "model_id": str}]``: known models only,
    ``after`` within 0..page_count, sorted, capped at MAX_MODEL_SLIDES."""
    slides = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        model_id = item.get("model_id")
        after = item.get("after")
        if model_id not in model_ids or isinstance(after, bool) or not isinstance(after, int):
            continue
        if after < 0 or (page_count is not None and after > page_count):
            continue
        slides.append({"after": after, "model_id": model_id})
        if len(slides) >= MAX_MODEL_SLIDES:
            break
    slides.sort(key=lambda s: s["after"])  # stable: keeps the order within a gap
    return slides


def _presenter_material_or_abort(slug: str, attachment_id: int) -> tuple[Paper, ProjectAttachment]:
    from app import active_paper_query

    project = active_paper_query().filter_by(slug=slug).first_or_404()
    if not can_edit_project(project):
        abort(403)
    if not project_supports_feature(project, "presentation_library"):
        abort(403)
    attachment = ProjectAttachment.query.filter_by(id=attachment_id, project_id=project.id).first_or_404()
    return project, attachment


def _model_payload(model: Model3D) -> dict:
    from app import model_asset_token
    from viewer_lighting import model_lighting

    hidden, faded = default_ar_materials(model)
    lighting = model_lighting(model)
    environment = lighting.get("environment")
    return {
        "id": model.id,
        "name": model.display_name or model.original_filename or "3D model",
        "src": url_for("serve_glb", unique_id=model.id, filename="model.glb") + f"?v={model_asset_token(model)}",
        "poster": url_for("serve_poster", unique_id=model.id) if model.poster_path else None,
        "hidden": sorted(hidden),
        "faded": faded,
        "exposure": lighting.get("exposure", 1),
        "shadow_intensity": lighting.get("shadow_intensity", 1),
        "shadow_softness": lighting.get("shadow_softness", 1),
        "environment": url_for("static", filename="environments/studio.webp") if environment == "studio" else environment,
    }


@presentation_bp.route("/p/<slug>/materials/<int:attachment_id>/present")
@login_required
def present_material(slug, attachment_id):
    project, attachment = _presenter_material_or_abort(slug, attachment_id)
    models = _presentable_models(project)
    slides = clean_model_slides(attachment.model_slides, None, {m.id for m in models})
    return render_template(
        "presenter.html",
        paper=project,
        attachment=attachment,
        pdf_url=(
            url_for("project_attachment_preview", slug=slug, attachment_id=attachment.id)
            if attachment.preview_pdf_path else None
        ),
        preview_pending=(not attachment.preview_pdf_path and attachment.preview_status in {"pending", "processing"}),
        models=[_model_payload(m) for m in models],
        model_slides=slides,
        save_url=url_for("presentation.save_model_slides", slug=slug, attachment_id=attachment.id),
        reader_url=url_for("project_attachment_reader", slug=slug, attachment_id=attachment.id),
    )


@presentation_bp.route("/p/<slug>/materials/<int:attachment_id>/present/slides", methods=["POST"])
@login_required
def save_model_slides(slug, attachment_id):
    project, attachment = _presenter_material_or_abort(slug, attachment_id)
    payload = request.get_json(silent=True) or {}
    page_count = payload.get("page_count")
    page_count = page_count if isinstance(page_count, int) and not isinstance(page_count, bool) and page_count >= 0 else None
    slides = clean_model_slides(payload.get("slides"), page_count, {m.id for m in _presentable_models(project)})
    attachment.model_slides = slides or None
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Could not save the 3D slides."}), 503
    return jsonify({"ok": True, "slides": slides})


@presentation_bp.route("/present-sw.js")
def presenter_service_worker():
    """Served from the site root so it may control the presenter pages
    (a worker under /static/js/ could only control /static/js/)."""
    response = send_from_directory(
        os.path.join(current_app.root_path, "static", "js"), "present-sw.js", mimetype="text/javascript",
    )
    response.headers["Cache-Control"] = "no-cache"
    return response
