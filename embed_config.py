"""Embed settings, saved per model by its owner or editors.

The embed code stays a plain iframe (``/view/<id>?embed=true``); what it shows
is read from ``Model3D.embed_config`` when the viewer renders, so changing the
settings updates every site that already embeds the model. The plan decides
how much of it applies: ``embed`` unlocks the settings at all,
``embed_full_layout`` the full viewer layout and ``embed_white_label`` hiding
AcademicAR's branding (admins set these per plan on /admin/pricing). Registered
in create_app.
"""

from html import escape

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from licensing import get_license_plan
from models import Model3D, db
from url_helpers import public_url
from utils.security import require_model_editor

embed_settings_bp = Blueprint("embed_settings", __name__)

TOOLS: tuple[tuple[str, str], ...] = (
    ("tour", "Tour"),
    ("measure", "Measure"),
    ("scenes", "Scenes"),
    ("layers", "Layers"),
    ("slice", "Slice"),
    ("ar", "View in AR"),
)
THEMES = ("dark", "light", "white")
LAYOUTS = ("compact", "full")
RATIOS = ("16:9", "4:3", "1:1", "3:4")
SIZE_MODES = ("fixed", "ratio", "fill")
HEIGHT_RANGE = (200, 2000)
WIDTH_RANGE = (200, 3000)

DEFAULTS: dict = {
    "layout": "compact",
    "tools": [key for key, _ in TOOLS],
    "theme": "dark",
    "title": True,
    "brand": True,
    # fixed: width px (0 = 100%) x height px; ratio: 100% wide at an aspect
    # ratio; fill: 100% x 100% of the page's own container.
    "size": {"mode": "fixed", "width": 0, "height": 480, "ratio": "16:9"},
}


def _int_in(value, low: int, high: int, what: str, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise ValueError(f"The {what} is invalid.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"The {what} is invalid.") from exc
    if allow_zero and number == 0:
        return 0
    if not low <= number <= high:
        raise ValueError(f"The {what} must be between {low} and {high} px.")
    return number


def clean_embed_config(payload) -> dict:
    """Whitelist a saved embed payload; raises ValueError for a bad one. Missing
    keys take their defaults."""
    if not isinstance(payload, dict):
        raise ValueError("The embed settings are invalid.")
    layout = payload.get("layout", DEFAULTS["layout"])
    theme = payload.get("theme", DEFAULTS["theme"])
    if layout not in LAYOUTS or theme not in THEMES:
        raise ValueError("The embed settings are invalid.")
    tools = payload.get("tools", DEFAULTS["tools"])
    known = {key for key, _ in TOOLS}
    if not isinstance(tools, list) or any(t not in known for t in tools):
        raise ValueError("The embed tools are invalid.")
    for flag in ("title", "brand"):
        if not isinstance(payload.get(flag, True), bool):
            raise ValueError("The embed settings are invalid.")
    size_in = payload.get("size", DEFAULTS["size"])
    if not isinstance(size_in, dict) or size_in.get("mode", "fixed") not in SIZE_MODES:
        raise ValueError("The embed size is invalid.")
    size = dict(DEFAULTS["size"])
    size["mode"] = size_in.get("mode", "fixed")
    if "width" in size_in:
        size["width"] = _int_in(size_in["width"], *WIDTH_RANGE, "width", allow_zero=True)
    if "height" in size_in:
        size["height"] = _int_in(size_in["height"], *HEIGHT_RANGE, "height")
    if size_in.get("ratio", size["ratio"]) not in RATIOS:
        raise ValueError("The embed size is invalid.")
    size["ratio"] = size_in.get("ratio", size["ratio"])
    return {
        "layout": layout,
        "tools": [key for key, _ in TOOLS if key in tools],
        "theme": theme,
        "title": payload.get("title", True),
        "brand": payload.get("brand", True),
        "size": size,
    }


def saved_embed_config(model) -> dict:
    """The model's saved settings (cleaned), or the defaults when none or invalid."""
    saved = getattr(model, "embed_config", None)
    if isinstance(saved, dict):
        try:
            return clean_embed_config(saved)
        except ValueError:
            pass
    return clean_embed_config({})


def embed_allowances(model) -> dict:
    """What the model's plan lets its embed do."""
    features = get_license_plan(model.license_type).features
    return {
        "customize": "embed" in features,
        "full_layout": "embed_full_layout" in features,
        "white_label": "embed_white_label" in features,
    }


def effective_embed_config(model, override: dict | None = None) -> dict:
    """What the embedded viewer applies: the saved settings (or ``override``,
    the owner's unsaved preview) limited to what the plan allows."""
    allowed = embed_allowances(model)
    if not allowed["customize"]:
        return clean_embed_config({})
    cfg = clean_embed_config(override) if override is not None else saved_embed_config(model)
    if not allowed["full_layout"]:
        cfg["layout"] = "compact"
    if not allowed["white_label"]:
        cfg["brand"] = True
    return cfg


def embed_snippet(model, cfg: dict) -> str:
    """The iframe code for ``cfg`` (size is the only part the code itself carries)."""
    size = cfg["size"]
    base = "border:0;border-radius:8px"
    if size["mode"] == "fill":
        width, height, style = "100%", "100%", base
    elif size["mode"] == "ratio":
        width, height = "100%", None
        style = f"{base};aspect-ratio:{size['ratio'].replace(':', '/')}"
    else:
        width = f"{size['width']}" if size["width"] else "100%"
        height, style = f"{size['height']}", base
    title = escape(f"{model.display_name or model.original_filename or '3D model'} (interactive 3D model)", quote=True)
    src = escape(public_url("view_model", model_id=model.id, embed="true"), quote=True)
    attrs = [f'src="{src}"', f'title="{title}"', f'width="{width}"']
    if height:
        attrs.append(f'height="{height}"')
    attrs += [f'style="{style}"', 'allow="xr-spatial-tracking; fullscreen"', "allowfullscreen", 'loading="lazy"']
    return "<iframe " + " ".join(attrs) + "></iframe>"


@embed_settings_bp.route("/models/<model_id>/embed", methods=["GET"])
@login_required
@require_model_editor
def embed_page(model_id):
    model = db.session.get(Model3D, model_id)
    if not model:
        abort(404)
    cfg = saved_embed_config(model)
    allowed = embed_allowances(model)
    return render_template(
        "embed_settings.html",
        model=model,
        paper=model.paper,
        cfg=cfg,
        allowed=allowed,
        tools=TOOLS,
        ratios=RATIOS,
        snippet=embed_snippet(model, effective_embed_config(model)),
    )


@embed_settings_bp.route("/models/<model_id>/embed/preview", methods=["POST"])
@login_required
@require_model_editor
def preview_embed(model_id):
    """The code for unsaved settings, so the Embed page never rebuilds it in JS."""
    model = db.session.get(Model3D, model_id)
    if not model:
        abort(404)
    try:
        cfg = clean_embed_config(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "snippet": embed_snippet(model, cfg)})


@embed_settings_bp.route("/models/<model_id>/embed", methods=["POST"])
@login_required
@require_model_editor
def save_embed(model_id):
    model = db.session.get(Model3D, model_id)
    if not model:
        abort(404)
    if not embed_allowances(model)["customize"]:
        return jsonify({"ok": False, "error": "Embed settings are not included in this model's plan."}), 403
    try:
        cfg = clean_embed_config(request.get_json(silent=True))
        if cfg["layout"] == "full" and not embed_allowances(model)["full_layout"]:
            raise ValueError("The full viewer layout is not included in this model's plan.")
        if not cfg["brand"] and not embed_allowances(model)["white_label"]:
            raise ValueError("Hiding AcademicAR branding is not included in this model's plan.")
        model.embed_config = cfg
        db.session.commit()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "The embed settings could not be saved. Please try again."}), 500
    return jsonify({"ok": True, "config": cfg, "snippet": embed_snippet(model, effective_embed_config(model))})
