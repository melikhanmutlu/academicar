"""Before / after comparison blueprint: two models side by side behind one link.

A comparison belongs to the user who created it and points at two ready models
from projects that user can edit. Creating one is a paid viewer feature
("comparison", checked on the left/primary model's plan). The public page
``/c/<public_id>`` lets each half obey its own model's visibility and access
window, so one private or expired half never takes the other down. Registered
in create_app next to the layer editor blueprint.
"""

import logging
import os

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from collaborators import can_edit_project, shared_projects_for
from layer_editor import model_layers
from licensing import get_license_plan, model_access_status, plan_supports_feature
from models import Model3D, ModelComparison, Paper, db
from url_helpers import public_url

logger = logging.getLogger(__name__)

comparisons_bp = Blueprint("comparisons", __name__)

TITLE_MAX = 120
LABEL_MAX = 60


def comparison_url(comparison) -> str:
    return public_url("comparisons.view", public_id=comparison.public_id)


def _model_label(model) -> str:
    return model.display_name or model.original_filename or "3D model"


def _can_manage(comparison) -> bool:
    if not current_user.is_authenticated:
        return False
    if comparison.owner_user_id == current_user.id:
        return True
    return bool(comparison.left_model and can_edit_project(comparison.left_model.paper))


@comparisons_bp.app_context_processor
def _inject_comparison_helpers():
    def model_comparisons(model) -> list:
        """Comparisons that include ``model`` (either side), newest first."""
        return (
            ModelComparison.query.filter(
                (ModelComparison.left_model_id == model.id) | (ModelComparison.right_model_id == model.id)
            )
            .order_by(ModelComparison.created_at.desc())
            .all()
        )

    return {
        "model_comparisons": model_comparisons,
        "comparison_url": comparison_url,
        "can_manage_comparison": _can_manage,
    }


def _editable_model(model_id):
    """The model when the current user may edit its project, else None."""
    model = db.session.get(Model3D, model_id) if model_id else None
    if model is None or model.paper is None or not can_edit_project(model.paper):
        return None
    return model


def _candidate_models(left):
    """The user's other ready models in projects they own or edit."""
    from app import active_paper_query

    papers = {p.id: p for p in active_paper_query().filter(Paper.user_id == current_user.id).all()}
    for paper in shared_projects_for(current_user):
        papers[paper.id] = paper
    if not papers:
        return []
    models = (
        Model3D.query.filter(Model3D.paper_id.in_(list(papers)), Model3D.id != left.id)
        .order_by(Model3D.created_at.desc())
        .all()
    )
    return [m for m in models if model_access_status(m) == "active"]


def _back_to_model(model):
    return redirect(url_for("model_edit", model_id=model.id) + "#compareSection")


def _plan_allows(model) -> bool:
    return plan_supports_feature(model.license_type, "comparison")


@comparisons_bp.route("/comparisons/new")
@login_required
def new_comparison():
    left = _editable_model(request.args.get("left"))
    if left is None:
        abort(404)
    if not _plan_allows(left):
        flash("Before / after comparison is available on paid plans.", "warning")
        return _back_to_model(left)
    if model_access_status(left) != "active":
        flash("Comparisons need a model that is ready and has active access.", "warning")
        return _back_to_model(left)
    return render_template(
        "comparison_new.html",
        left=left,
        candidates=_candidate_models(left),
        title_max=TITLE_MAX,
        label_max=LABEL_MAX,
        model_label=_model_label,
    )


@comparisons_bp.route("/comparisons", methods=["POST"])
@login_required
def create_comparison():
    from app import log_audit, new_public_id

    left = _editable_model((request.form.get("left_model_id") or "").strip())
    if left is None:
        abort(404)
    back = _back_to_model(left)
    right = _editable_model((request.form.get("right_model_id") or "").strip())
    if right is None:
        flash("Pick a second model from a project you can edit.", "danger")
        return redirect(url_for("comparisons.new_comparison", left=left.id))
    if right.id == left.id:
        flash("Pick two different models to compare.", "danger")
        return redirect(url_for("comparisons.new_comparison", left=left.id))
    if not _plan_allows(left):
        flash("Before / after comparison is available on paid plans.", "warning")
        return back
    if model_access_status(left) != "active" or model_access_status(right) != "active":
        flash("Both models must be ready, with active access, to compare them.", "danger")
        return redirect(url_for("comparisons.new_comparison", left=left.id))

    title = (request.form.get("title") or "").strip()
    left_label = (request.form.get("left_label") or "").strip() or "Before"
    right_label = (request.form.get("right_label") or "").strip() or "After"
    if not title:
        title = f"{_model_label(left)} vs {_model_label(right)}"[:TITLE_MAX]
    if len(title) > TITLE_MAX:
        flash(f"The title can be at most {TITLE_MAX} characters.", "danger")
        return redirect(url_for("comparisons.new_comparison", left=left.id))
    if len(left_label) > LABEL_MAX or len(right_label) > LABEL_MAX:
        flash(f"Labels can be at most {LABEL_MAX} characters.", "danger")
        return redirect(url_for("comparisons.new_comparison", left=left.id))

    comparison = ModelComparison(
        public_id=new_public_id(),
        owner_user_id=current_user.id,
        title=title,
        left_model_id=left.id,
        right_model_id=right.id,
        left_label=left_label,
        right_label=right_label,
        sync_camera=request.form.get("sync_camera") == "on",
    )
    db.session.add(comparison)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Comparison create failed")
        flash("The comparison could not be saved. Please try again.", "danger")
        return back
    log_audit(
        "comparison_created",
        user_id=current_user.id,
        resource_id=comparison.public_id,
        details={"left_model_id": left.id, "right_model_id": right.id},
    )
    flash("Comparison created. Share its link or print its QR label.", "success")
    return back


@comparisons_bp.route("/comparisons/<int:cid>/delete", methods=["POST"])
@login_required
def delete_comparison(cid):
    from app import log_audit

    comparison = db.session.get(ModelComparison, cid)
    if comparison is None:
        abort(404)
    if not _can_manage(comparison):
        abort(403)
    back = _back_to_model(comparison.left_model)
    public_id = comparison.public_id
    try:
        db.session.delete(comparison)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.exception("Comparison delete failed")
        flash("The comparison could not be deleted. Please try again.", "danger")
        return back
    log_audit("comparison_deleted", user_id=current_user.id, resource_id=public_id)
    flash("Comparison deleted.", "info")
    return back


def _half(model, label):
    """What one side of the public page shows; ``available`` is False when the
    model's project is not visible to this request or its access window is
    closed. Nothing about an unavailable model is exposed."""
    from app import model_asset_token, paper_visible_to_request
    from services.r2_mirror import ensure_local

    visible = bool(model is not None and model.paper is not None and paper_visible_to_request(model.paper))
    status = model_access_status(model) if model is not None else "deleted"
    half = {"label": label, "available": visible and status == "active", "visible": visible, "status": status}
    if not half["available"]:
        return half
    plan = get_license_plan(model.license_type)
    token = model_asset_token(model)
    usdz_url = None
    if "ar" in plan.features:
        usdz_path = os.path.join(current_app.config["CONVERTED_FOLDER"], model.id, "model.usdz")
        if not os.path.exists(usdz_path):
            ensure_local(usdz_path, f"converted/{model.id}/model.usdz")
        if os.path.exists(usdz_path):
            usdz_url = url_for("serve_glb", unique_id=model.id, filename="model.usdz") + f"?v={token}"
    layers = model_layers(model)
    half.update(
        model=model,
        name=_model_label(model),
        ar="ar" in plan.features,
        glb_url=url_for("serve_glb", unique_id=model.id, filename="model.glb") + f"?v={token}",
        usdz_url=usdz_url,
        poster_url=url_for("serve_poster", unique_id=model.id) if model.poster_path else None,
        layers=[
            {"name": str(layer.get("name") or ""), "color": layer.get("color"), "materials": layer.get("materials")}
            for layer in layers
        ]
        if len(layers) > 1
        else [],
    )
    return half


@comparisons_bp.route("/c/<public_id>")
def view(public_id):
    from app import paper_visible_to_request, private_project_response, track_event

    comparison = ModelComparison.query.filter_by(public_id=public_id).first()
    if comparison is None:
        abort(404)
    left_model, right_model = comparison.left_model, comparison.right_model
    is_owner = current_user.is_authenticated and current_user.id == comparison.owner_user_id

    # The paid feature must still be on for at least one model's plan.
    if not any(_plan_allows(m) for m in (left_model, right_model) if m is not None):
        return _unavailable(is_owner)

    left = _half(left_model, comparison.left_label or "Before")
    right = _half(right_model, comparison.right_label or "After")
    if not left["available"] and not right["available"]:
        if not left["visible"] and not right["visible"]:
            return private_project_response(left_model.paper)
        return _unavailable(is_owner)

    editor = current_user.is_authenticated and (
        is_owner or bool(current_user.is_admin) or any(can_edit_project(m.paper) for m in (left_model, right_model))
    )
    if not editor:
        track_event(
            "comparison_viewed",
            owner_user_id=comparison.owner_user_id,
            project_id=left_model.paper_id,
            model_id=left_model.id,
            properties={"comparison": comparison.public_id},
        )
    return render_template(
        "compare.html",
        comparison=comparison,
        left=left,
        right=right,
        is_owner=is_owner,
        canonical=comparison_url(comparison),
    )


def _unavailable(is_owner):
    return (
        render_template("model_access_unavailable.html", model=None, paper=None, status="unavailable", is_owner=is_owner),
        410,
    )


@comparisons_bp.route("/qr-print/comparison/<int:cid>/<asset>")
@login_required
def qr_asset(cid, asset):
    from app import qr_asset_response

    comparison = db.session.get(ModelComparison, cid)
    if comparison is None:
        abort(404)
    if not _can_manage(comparison):
        abort(403)
    return qr_asset_response(asset, comparison_url(comparison), comparison.title, f"qr-compare-{comparison.public_id}")
