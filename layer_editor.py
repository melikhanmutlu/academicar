"""Layer editor blueprint: owners and editors rename and recolour viewer layers.

Segmentation label maps arrive as "Label 1", "Label 2"; STEP parts keep their
CAD names. A layer's display name lives in ``Model3D.layer_info`` only: its
``materials`` (what the viewer looks up in the GLB) never change, so renaming
needs no GLB rewrite. A colour change rewrites just that layer's materials'
``baseColorFactor`` in the stored GLB (pygltflib leaves Draco geometry alone).
Registered in create_app next to the institution blueprint.
"""

import csv
import io
import logging
import os
import re
import shutil

from flask import Blueprint, Response, abort, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required
from pygltflib import GLTF2
from sqlalchemy.exc import SQLAlchemyError

from converters.layer_metrics import rename_metrics_layers, restrict_metrics
from licensing import plan_supports_feature
from converters.stl_converter import _srgb_to_linear
from models import Model3D, db
from utils.security import require_model_editor

logger = logging.getLogger(__name__)

layer_editor_bp = Blueprint("layer_editor", __name__)

LAYER_NAME_MAX = 80
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_BACKUP_SUFFIX = ".layers_backup"


def model_layers(model) -> list[dict]:
    """The model's viewer layers (``[]`` when it has none)."""
    info = model.layer_info
    layers = info.get("layers") if isinstance(info, dict) else None
    return [layer for layer in layers if isinstance(layer, dict)] if isinstance(layers, list) else []


def model_has_layers(model) -> bool:
    """True when the viewer shows a Layers panel, i.e. colours are set per layer."""
    return len(model_layers(model)) >= 2


def layer_metrics_enabled(model) -> bool:
    """Per-layer measurements are a paid-plan feature."""
    return plan_supports_feature(model.license_type, "layer_metrics")


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _point(value):
    if isinstance(value, (list, tuple)) and len(value) == 3 and all(_number(v) is not None for v in value):
        return [float(v) for v in value]
    return None


def model_layer_metrics(model) -> dict | None:
    """Stored measurements for the model's layers: ``{"layers": {name: {...}}, "pairs":
    [...] shortest first, "truncated": bool}``. ``None`` when missing or unusable."""
    info = model.layer_info if isinstance(model.layer_info, dict) else {}
    metrics = restrict_metrics(info.get("metrics"), [layer.get("name") for layer in model_layers(model)])
    if not metrics:
        return None
    layers = {}
    for name, item in metrics["layers"].items():
        item = item if isinstance(item, dict) else {}
        dims = item.get("dims_mm")
        dims = [float(d) for d in dims if _number(d) is not None] if isinstance(dims, list) else []
        layers[name] = {"dims_mm": dims if len(dims) == 3 else None, "max_diameter_mm": _number(item.get("max_diameter_mm"))}
    pairs = []
    for pair in metrics["pairs"]:
        if not isinstance(pair, dict) or _number(pair.get("min_distance_mm")) is None:
            continue
        point_a, point_b = _point(pair.get("point_a")), _point(pair.get("point_b"))
        if point_a and point_b and pair["a"] != pair["b"]:
            pairs.append({"a": pair["a"], "b": pair["b"], "min_distance_mm": float(pair["min_distance_mm"]),
                          "point_a": point_a, "point_b": point_b})
    pairs.sort(key=lambda p: p["min_distance_mm"])
    return {"layers": layers, "pairs": pairs, "truncated": bool(metrics.get("truncated"))}


def viewer_layer_metrics(model, can_edit: bool) -> dict | None:
    """Metrics the viewer page may embed: the plan must include them and they must be
    public (``metrics_public``) unless the viewer can edit the project. Layer sizes
    and the pair points only: no centroids or raw dims."""
    if not layer_metrics_enabled(model) or not model_has_layers(model):
        return None
    if not (can_edit or model.layer_info.get("metrics_public")):
        return None
    metrics = model_layer_metrics(model)
    if not metrics:
        return None
    return {
        "layers": {name: {"max_diameter_mm": item["max_diameter_mm"]} for name, item in metrics["layers"].items()},
        "pairs": metrics["pairs"],
        "truncated": metrics["truncated"],
    }


@layer_editor_bp.app_context_processor
def _inject_layer_helpers():
    def processing_notes(model) -> list[str]:
        info = model.layer_info
        notes = info.get("notes") if isinstance(info, dict) else None
        return [str(note) for note in notes if note] if isinstance(notes, list) else []

    return {
        "model_layers": model_layers,
        "model_has_layers": model_has_layers,
        "model_processing_notes": processing_notes,
        "model_layer_metrics": model_layer_metrics,
        "layer_metrics_enabled": layer_metrics_enabled,
    }


def _rgba_from_hex(hex_color: str, alpha: float) -> list[float]:
    r, g, b = (_srgb_to_linear(int(hex_color[i:i + 2], 16) / 255.0) for i in (1, 3, 5))
    return [r, g, b, alpha]


def _write_layer_colors(glb_path: str, colors: dict[str, str]) -> None:
    """Set baseColorFactor (hex is sRGB, the glTF factor linear) on named materials."""
    gltf = GLTF2.load(glb_path)
    found = set()
    for material in gltf.materials or []:
        hex_color = colors.get(material.name)
        pbr = material.pbrMetallicRoughness
        if hex_color is None or pbr is None or pbr.baseColorTexture is not None:
            continue
        previous = pbr.baseColorFactor or [1.0, 1.0, 1.0, 1.0]
        pbr.baseColorFactor = _rgba_from_hex(hex_color, previous[3] if len(previous) > 3 else 1.0)
        found.add(material.name)
    if found != set(colors):
        raise ValueError("Layer materials missing from the model file.")
    gltf.save(glb_path)


@layer_editor_bp.route("/models/<model_id>/layers", methods=["POST"])
@login_required
@require_model_editor
def update_layers(model_id):
    from app import _refresh_model_poster, enqueue_conversion_job, log_audit
    from services.r2_mirror import ensure_local, mirror_file

    model = db.session.get(Model3D, model_id)
    if not model:
        abort(404)
    layers = model_layers(model)
    if len(layers) < 2:
        abort(404)
    back = redirect(url_for("model_edit", model_id=model.id))
    if (model.processing_status or "ready") not in ("ready", "replacement_failed"):
        flash("Layers can be edited once the model finishes processing.", "warning")
        return back

    names, seen, new_colors = [], set(), {}
    for index, layer in enumerate(layers):
        name = (request.form.get(f"layer_name_{index}") or "").strip()
        if not name:
            flash("Every layer needs a name.", "danger")
            return back
        if len(name) > LAYER_NAME_MAX:
            flash(f"Layer names can be at most {LAYER_NAME_MAX} characters.", "danger")
            return back
        if name.casefold() in seen:
            flash(f"Layer names must be unique (\"{name}\" is used twice).", "danger")
            return back
        seen.add(name.casefold())
        names.append(name)

        submitted = (request.form.get(f"layer_color_{index}") or "").strip()
        if layer.get("color") and submitted:
            if _HEX_COLOR.fullmatch(submitted) is None:
                flash("Colours must be hex values like #RRGGBB.", "danger")
                return back
            if submitted.lower() != str(layer["color"]).lower():
                new_colors[index] = submitted.lower()

    glb_path = model.glb_path
    backup_path = glb_path + _BACKUP_SUFFIX
    refreshed_poster = None
    if new_colors:
        # Restore the working GLB from the R2 mirror if the local copy is gone.
        ensure_local(glb_path, f"converted/{model.id}/model.glb")
        try:
            shutil.copy2(glb_path, backup_path)
            by_material = {material: new_colors[i] for i in new_colors for material in layers[i].get("materials") or []}
            _write_layer_colors(glb_path, by_material)
        except Exception:
            logger.exception("Layer colour update failed for model %s; restoring the previous GLB", model.id)
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, glb_path)
            flash("The layer colours could not be saved. The previous version is still active.", "warning")
            return back
        finally:
            if os.path.exists(backup_path):
                os.remove(backup_path)

    updated = []
    for index, layer in enumerate(layers):
        item = dict(layer, name=names[index])
        if index in new_colors:
            item["color"] = new_colors[index]
        updated.append(item)
    info = dict(model.layer_info)
    info["layers"] = updated
    if info.get("metrics"):  # measurements are keyed by layer name
        renames = {l["name"]: names[i] for i, l in enumerate(layers) if l.get("name") != names[i]}
        if renames:
            info["metrics"] = rename_metrics_layers(info["metrics"], renames)
    model.layer_info = info  # new dict: JSON columns do not track in-place changes
    try:
        if new_colors:
            model.file_size = os.path.getsize(glb_path)  # part of model_asset_token
            refreshed_poster = _refresh_model_poster(model)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Layer update could not be saved for model %s", model.id)
        flash("The layers could not be saved.", "danger")
        return back

    if new_colors:
        mirror_file(glb_path, f"converted/{model.id}/model.glb")
        if refreshed_poster:
            mirror_file(refreshed_poster, f"converted/{model.id}/poster.png")
        # The iOS USDZ is built from the GLB: regenerate it in the worker so AR
        # shows the new colours (the colours are already saved if this fails).
        usdz_path = os.path.join(current_app.config["CONVERTED_FOLDER"], model.id, "model.usdz")
        try:
            enqueue_conversion_job(
                current_app._get_current_object(),
                model=model,
                job_kwargs={"model_id": model.id, "glb_path": model.glb_path, "usdz_path": usdz_path},
                job_type="usdz_regen",
            )
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not enqueue USDZ regen after layer recolour for %s", model.id)

    log_audit(
        "model_layers_updated",
        user_id=current_user.id,
        resource_id=model.id,
        details={"layers": len(updated), "recoloured": len(new_colors)},
    )
    flash("Layers saved.", "success")
    return back


def _csv_cell(value):
    """Spreadsheet-safe cell: text that would run as a formula gets a leading quote."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _metrics_model_or_404(model_id):
    model = db.session.get(Model3D, model_id)
    if not model or not model_has_layers(model):
        abort(404)
    return model


@layer_editor_bp.route("/models/<model_id>/layer-metrics.csv")
@login_required
@require_model_editor
def layer_metrics_csv(model_id):
    model = _metrics_model_or_404(model_id)
    if not layer_metrics_enabled(model):
        abort(403)
    metrics = model_layer_metrics(model)
    if not metrics:
        abort(404)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["kind", "layer", "layer_b", "length_mm", "width_mm", "height_mm", "max_diameter_mm", "volume_ml", "shortest_distance_mm"])
    layers = {layer["name"]: layer for layer in model_layers(model)}
    for name, item in metrics["layers"].items():
        volume = _number(layers.get(name, {}).get("volume_ml"))
        writer.writerow([
            "layer", _csv_cell(name), "", *(item["dims_mm"] or ["", "", ""]),
            "" if item["max_diameter_mm"] is None else item["max_diameter_mm"], "" if volume is None else volume, "",
        ])
    for pair in metrics["pairs"]:
        writer.writerow(["pair", _csv_cell(pair["a"]), _csv_cell(pair["b"]), "", "", "", "", "", pair["min_distance_mm"]])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="layer-measurements-{model.id[:8]}.csv"', "Cache-Control": "no-store"},
    )


@layer_editor_bp.route("/models/<model_id>/layer-metrics/visibility", methods=["POST"])
@login_required
@require_model_editor
def update_layer_metrics_visibility(model_id):
    model = _metrics_model_or_404(model_id)
    back = redirect(url_for("model_edit", model_id=model.id) + "#measurementsSection")
    make_public = request.form.get("metrics_public") == "1"
    if make_public and not layer_metrics_enabled(model):
        flash("Measurements are available on paid plans.", "warning")
        return back
    info = dict(model.layer_info)
    info["metrics_public"] = make_public  # new dict: JSON columns do not track in-place changes
    model.layer_info = info
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Measurement visibility could not be saved for model %s", model.id)
        flash("The setting could not be saved.", "danger")
        return back
    flash("Viewers can now see measurements." if make_public else "Measurements are now visible to editors only.", "success")
    return back
