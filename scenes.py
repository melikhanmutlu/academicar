"""Scenes blueprint: saved views of a model, each with its own link and QR code.

A scene stores the viewer's view state (visible layers and their opacity,
camera, labels, background and an optional section plane) as a small JSON
object in ``ModelScene.state``. The browser captures it with
``window.viewerView.getState()`` and this module whitelists it before saving.
``/s/<public_id>`` resolves a scene like the stable ``/m/<public_id>`` QR
resolver does and redirects to ``/view/<model_id>?scene=<id>``; the viewer then
applies the saved state. Scenes are a paid-plan feature (plan key "scenes");
when a plan no longer has it the link falls back to the plain viewer.
Registered in create_app next to the layer editor blueprint.
"""

import json
import logging
import math
import re

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from layer_editor import model_layers
from licensing import model_access_status, plan_supports_feature
from models import Model3D, ModelScene, db
from utils.security import require_model_editor

logger = logging.getLogger(__name__)

scenes_bp = Blueprint("scenes", __name__)

MAX_SCENES_PER_MODEL = 12
TITLE_MAX = 120
DESCRIPTION_MAX = 500
STATE_MAX_BYTES = 16 * 1024

# One model-viewer value: "auto" or a number with a unit (exponent notation
# appears for tiny values when JavaScript prints a number).
_TOKEN = r"(?:auto|-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?:rad|deg|mm|cm|m|%))"
_CAMERA_VALUE = re.compile(rf"{_TOKEN}(?: {_TOKEN}){{2}}")
_FOV_VALUE = re.compile(_TOKEN)
_CAMERA_LIMITS = {"orbit": (_CAMERA_VALUE, 64), "target": (_CAMERA_VALUE, 96), "fov": (_FOV_VALUE, 16)}
_BACKGROUNDS = ("dark", "light", "white")
_SECTION_OFFSET_LIMIT = 1000.0


def _number(value):
    """A finite float from a JSON number (booleans and NaN/inf are refused)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def clean_scene_state(state, model) -> dict:
    """Whitelist and normalise a view state; raises ValueError for non-dict or
    oversize input. Unknown layers, malformed camera values and unknown keys
    are dropped rather than rejected, so a viewer upgrade never breaks saving."""
    if not isinstance(state, dict):
        raise ValueError("The view state must be an object.")
    try:
        size = len(json.dumps(state))
    except (TypeError, ValueError) as exc:
        raise ValueError("The view state is invalid.") from exc
    if size > STATE_MAX_BYTES:
        raise ValueError("The view state is too large.")

    cleaned = {"v": 1}
    if isinstance(state.get("labels"), bool):
        cleaned["labels"] = state["labels"]

    layers = state.get("layers")
    if isinstance(layers, dict):
        known = {str(layer.get("name")) for layer in model_layers(model)}
        kept = {}
        for name, entry in layers.items():
            if name not in known or not isinstance(entry, dict):
                continue
            opacity = _number(entry.get("opacity"))
            kept[name] = {
                "visible": entry.get("visible") is not False,
                "opacity": 1.0 if opacity is None else round(min(1.0, max(0.0, opacity)), 2),
            }
        if kept:
            cleaned["layers"] = kept

    camera = state.get("camera")
    if isinstance(camera, dict):
        kept = {}
        for key, (pattern, max_len) in _CAMERA_LIMITS.items():
            value = camera.get(key)
            if isinstance(value, str) and len(value) <= max_len and pattern.fullmatch(value):
                kept[key] = value
        if "orbit" in kept:  # a camera without an orbit cannot be restored
            cleaned["camera"] = kept

    if state.get("background") in _BACKGROUNDS:
        cleaned["background"] = state["background"]

    section = state.get("section")
    if isinstance(section, dict) and section.get("axis") in ("x", "y", "z"):
        offset = _number(section.get("offset"))
        cleaned["section"] = {
            "axis": section["axis"],
            "offset": round(min(_SECTION_OFFSET_LIMIT, max(-_SECTION_OFFSET_LIMIT, offset or 0.0)), 4),
            "flip": section.get("flip") is True,
        }
    return cleaned


def scenes_enabled(model) -> bool:
    return plan_supports_feature(model.license_type, "scenes")


def scene_url(scene: ModelScene) -> str:
    from url_helpers import public_url

    return public_url("scenes.scene_resolver", public_id=scene.public_id)


def scene_is_stale(scene: ModelScene, model) -> bool:
    """True when the scene names layers the model no longer has (for example
    after a model replacement), so applying it cannot restore the saved view."""
    saved = (scene.state or {}).get("layers")
    if not isinstance(saved, dict) or not saved:
        return False
    return not set(saved) <= {str(layer.get("name")) for layer in model_layers(model)}


def scene_to_dict(scene: ModelScene, model=None) -> dict:
    model = model or scene.model
    return {
        "id": scene.id,
        "public_id": scene.public_id,
        "title": scene.title,
        "description": scene.description or "",
        "order_index": scene.order_index,
        "state": scene.state or {"v": 1},
        "url": scene_url(scene),
        "qr_label_url": url_for("scenes.scene_qr_asset", scene_id=scene.id, asset="label.png"),
        "stale": scene_is_stale(scene, model),
    }


def scenes_for_viewer(model) -> list[dict]:
    """The model's scenes for the viewer (empty when the plan lacks scenes)."""
    if not scenes_enabled(model):
        return []
    return [scene_to_dict(scene, model) for scene in model.scenes]


@scenes_bp.app_context_processor
def _inject_scene_helpers():
    return {"scene_dict": scene_to_dict, "scenes_enabled": scenes_enabled}


def _error(message, status):
    return jsonify({"error": message}), status


def _load_model(model_id):
    model = db.session.get(Model3D, model_id)
    if not model:
        abort(404)
    return model


def _clean_text(payload, key, limit, required=False):
    """(value, error) for a title/description field."""
    value = payload.get(key)
    if value is None:
        return (None, "A scene needs a title." if required else None)
    if not isinstance(value, str):
        return (None, f"Invalid {key}.")
    value = " ".join(value.split()) if key == "title" else value.strip()
    if required and not value:
        return (None, "A scene needs a title.")
    if len(value) > limit:
        return (None, f"The {key} can be at most {limit} characters.")
    return (value, None)


def _json_payload():
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else None


@scenes_bp.route("/models/<model_id>/scenes", methods=["POST"])
@login_required
@require_model_editor
def create_scene(model_id):
    from app import log_audit, new_public_id

    model = _load_model(model_id)
    if not scenes_enabled(model):
        return _error("Saved scenes are available on paid plans.", 403)
    payload = _json_payload()
    if payload is None:
        return _error("Invalid request.", 400)
    title, error = _clean_text(payload, "title", TITLE_MAX, required=True)
    if error:
        return _error(error, 400)
    description, error = _clean_text(payload, "description", DESCRIPTION_MAX)
    if error:
        return _error(error, 400)
    try:
        state = clean_scene_state(payload.get("state"), model)
    except ValueError as exc:
        return _error(str(exc), 400)
    existing = list(model.scenes)
    if len(existing) >= MAX_SCENES_PER_MODEL:
        return _error(f"A model can have at most {MAX_SCENES_PER_MODEL} scenes. Delete one to save another.", 400)

    scene = ModelScene(
        model_id=model.id,
        public_id=new_public_id(),
        title=title,
        description=description or None,
        order_index=max((s.order_index for s in existing), default=-1) + 1,
        state=state,
        created_by_user_id=current_user.id,
    )
    db.session.add(scene)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Scene could not be saved for model %s", model.id)
        return _error("The scene could not be saved.", 500)
    log_audit("model_scene_created", user_id=current_user.id, resource_id=model.id, details={"scene_id": scene.id})
    return jsonify({"scene": scene_to_dict(scene, model)}), 201


@scenes_bp.route("/models/<model_id>/scenes/reorder", methods=["POST"])
@login_required
@require_model_editor
def reorder_scenes(model_id):
    model = _load_model(model_id)
    if not scenes_enabled(model):
        return _error("Saved scenes are available on paid plans.", 403)
    payload = _json_payload()
    order = payload.get("order") if payload else None
    by_id = {scene.id: scene for scene in model.scenes}
    if (
        not isinstance(order, list)
        or any(isinstance(i, bool) or not isinstance(i, int) for i in order)
        or sorted(order) != sorted(by_id)
    ):
        return _error("The order must list every scene of this model once.", 400)
    for index, scene_id in enumerate(order):
        by_id[scene_id].order_index = index
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Scene order could not be saved for model %s", model.id)
        return _error("The order could not be saved.", 500)
    db.session.refresh(model)
    return jsonify({"scenes": [scene_to_dict(scene, model) for scene in model.scenes]})


def _model_scene_or_404(model, scene_id):
    scene = db.session.get(ModelScene, scene_id)
    if not scene or scene.model_id != model.id:
        abort(404)
    return scene


@scenes_bp.route("/models/<model_id>/scenes/<int:scene_id>", methods=["POST"])
@login_required
@require_model_editor
def update_scene(model_id, scene_id):
    from app import log_audit

    model = _load_model(model_id)
    scene = _model_scene_or_404(model, scene_id)
    if not scenes_enabled(model):
        return _error("Saved scenes are available on paid plans.", 403)
    payload = _json_payload()
    if payload is None:
        return _error("Invalid request.", 400)
    if "title" in payload:
        title, error = _clean_text(payload, "title", TITLE_MAX, required=True)
        if error:
            return _error(error, 400)
        scene.title = title
    if "description" in payload:
        description, error = _clean_text(payload, "description", DESCRIPTION_MAX)
        if error:
            return _error(error, 400)
        scene.description = description or None
    if payload.get("state") is not None:
        try:
            scene.state = clean_scene_state(payload["state"], model)
        except ValueError as exc:
            return _error(str(exc), 400)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Scene %s could not be updated", scene_id)
        return _error("The scene could not be saved.", 500)
    log_audit("model_scene_updated", user_id=current_user.id, resource_id=model.id, details={"scene_id": scene.id})
    return jsonify({"scene": scene_to_dict(scene, model)})


@scenes_bp.route("/models/<model_id>/scenes/<int:scene_id>/delete", methods=["POST"])
@login_required
@require_model_editor
def delete_scene(model_id, scene_id):
    from app import log_audit

    model = _load_model(model_id)
    scene = _model_scene_or_404(model, scene_id)
    # Deleting stays allowed after a plan change so old scenes can be removed.
    db.session.delete(scene)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Scene %s could not be deleted", scene_id)
        return _error("The scene could not be deleted.", 500)
    log_audit("model_scene_deleted", user_id=current_user.id, resource_id=model.id, details={"scene_id": scene_id})
    return jsonify({"ok": True})


@scenes_bp.route("/s/<public_id>")
def scene_resolver(public_id):
    """Public scene link. Same access rules as the model QR resolver: always a
    302 to the viewer, the 410/403 explanation page, or a 404."""
    from analytics import track_event
    from app import paper_visible_to_request, private_project_response

    scene = ModelScene.query.filter_by(public_id=public_id).first()
    if not scene:
        abort(404)
    model = scene.model
    if not model or not model.paper:
        abort(404)
    track_event("qr_scanned", owner_user_id=model.user_id, project_id=model.paper_id, model_id=model.id)
    if not paper_visible_to_request(model.paper):
        return private_project_response(model.paper)
    status = model_access_status(model)
    if status != "active":
        is_owner = current_user.is_authenticated and current_user.id == model.user_id
        return (
            render_template(
                "model_access_unavailable.html", model=model, paper=model.paper, status=status, is_owner=is_owner,
            ),
            410,
        )
    if not scenes_enabled(model):
        return redirect(url_for("view_model", model_id=model.id))
    return redirect(url_for("view_model", model_id=model.id, scene=scene.id))


@scenes_bp.route("/qr-print/scene/<int:scene_id>/<asset>")
@login_required
def scene_qr_asset(scene_id, asset):
    """Print-quality QR downloads for one scene link (editors only)."""
    from app import qr_asset_response
    from collaborators import can_edit_project

    scene = db.session.get(ModelScene, scene_id)
    if not scene or not scene.model or not scene.model.paper:
        abort(404)
    if not can_edit_project(scene.model.paper):
        abort(403)
    return qr_asset_response(asset, scene_url(scene), scene.title, f"qr-scene-{scene.public_id}")
