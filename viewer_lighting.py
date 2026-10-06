"""Viewer lighting presets, saved per model by its owner or editors.

The viewer applies a preset through model-viewer's ``exposure``,
``shadow-intensity``, ``shadow-softness`` and ``environment-image``; the owner
can fine-tune exposure and shadows and save the result on
``Model3D.viewer_lighting``. Presets follow the web_ar project's set, with the
studio environment map AcademicAR already ships. Registered in create_app.
"""

from flask import Blueprint, jsonify, request
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from models import Model3D, db
from utils.security import require_model_editor

viewer_lighting_bp = Blueprint("viewer_lighting", __name__)

# environment: "studio" = static/environments/studio.webp; "neutral" / "legacy"
# are model-viewer's built-in lighting.
LIGHTING_PRESETS: dict[str, dict] = {
    "studio": {"label": "Studio", "environment": "studio", "exposure": 1.0, "shadow_intensity": 1.0, "shadow_softness": 0.7},
    "soft": {"label": "Soft", "environment": "studio", "exposure": 1.1, "shadow_intensity": 0.6, "shadow_softness": 1.0},
    "dramatic": {"label": "Dramatic", "environment": "neutral", "exposure": 0.75, "shadow_intensity": 2.0, "shadow_softness": 0.2},
    "bright": {"label": "Bright", "environment": "studio", "exposure": 1.5, "shadow_intensity": 0.8, "shadow_softness": 0.8},
    "flat": {"label": "Flat", "environment": "legacy", "exposure": 1.0, "shadow_intensity": 0.0, "shadow_softness": 1.0},
    "dim": {"label": "Dim", "environment": "neutral", "exposure": 0.55, "shadow_intensity": 1.5, "shadow_softness": 0.5},
}
DEFAULT_PRESET = "studio"
_RANGES = {"exposure": (0.1, 3.0), "shadow_intensity": (0.0, 3.0), "shadow_softness": (0.0, 1.0)}


def model_lighting(model) -> dict:
    """The lighting the viewer opens with: the saved one, else the default preset."""
    saved = model.viewer_lighting if isinstance(getattr(model, "viewer_lighting", None), dict) else {}
    preset = saved.get("preset") if saved.get("preset") in LIGHTING_PRESETS else DEFAULT_PRESET
    lighting = {"preset": preset, **{k: v for k, v in LIGHTING_PRESETS[preset].items() if k != "label"}}
    for key, (low, high) in _RANGES.items():
        value = saved.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            lighting[key] = round(min(high, max(low, float(value))), 2)
    return lighting


def clean_lighting(payload) -> dict:
    """Whitelist a saved lighting payload; raises ValueError for a bad one."""
    if not isinstance(payload, dict) or payload.get("preset") not in LIGHTING_PRESETS:
        raise ValueError("Choose a lighting preset.")
    cleaned = {"preset": payload["preset"]}
    for key, (low, high) in _RANGES.items():
        value = payload.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("The lighting values are invalid.") from exc
        if number != number:  # NaN
            raise ValueError("The lighting values are invalid.")
        cleaned[key] = round(min(high, max(low, number)), 2)
    return cleaned


@viewer_lighting_bp.route("/models/<model_id>/lighting", methods=["POST"])
@login_required
@require_model_editor
def save_lighting(model_id):
    model = db.session.get(Model3D, model_id)
    try:
        model.viewer_lighting = clean_lighting(request.get_json(silent=True))
        db.session.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "The lighting could not be saved. Please try again."}), 500
    return jsonify({"ok": True, "lighting": model_lighting(model)})


@viewer_lighting_bp.app_context_processor
def _inject_lighting_helpers():
    return {"model_lighting": model_lighting, "lighting_presets": LIGHTING_PRESETS}
