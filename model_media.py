"""Saved images and videos of a model, for its owner and project editors.

The viewer uploads what the owner captures (screenshots, figures, recordings)
and, once after processing, what the owner's browser generates automatically
(eight views and short showcase videos; source "auto", replaced when generated
again). Files live in converted/<model_id>/media/ and are mirrored to R2.
Media is private to people who can edit the project. Registered in create_app.
"""

import io
import logging
import os
import uuid

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory, url_for
from flask_login import login_required
from sqlalchemy.exc import SQLAlchemyError

from models import Model3D, ModelMedia, db
from utils.security import require_model_editor

logger = logging.getLogger(__name__)

model_media_bp = Blueprint("model_media", __name__)

MAX_IMAGES_PER_MODEL = 24
MAX_VIDEOS_PER_MODEL = 8
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 60 * 1024 * 1024
LABEL_MAX = 120
_TYPES = {
    "image/png": ("image", "png"),
    "image/jpeg": ("image", "jpg"),
    "image/webp": ("image", "webp"),
    "video/mp4": ("video", "mp4"),
    "video/webm": ("video", "webm"),
}


def media_folder(model_id: str) -> str:
    return os.path.join(current_app.config["CONVERTED_FOLDER"], model_id, "media")


def media_r2_key(media: ModelMedia) -> str:
    return f"converted/{media.model_id}/media/{media.filename}"


def media_to_dict(media: ModelMedia) -> dict:
    url = url_for("model_media.serve_media", model_id=media.model_id, media_id=media.id)
    return {
        "id": media.id,
        "kind": media.kind,
        "source": media.source,
        "label": media.label or "",
        "size": media.file_size,
        "created_at": media.created_at.isoformat() if media.created_at else None,
        "url": url,
        "download_url": url + "?download=1",
    }


def model_media_list(model) -> list[dict]:
    """Newest first."""
    return [media_to_dict(item) for item in model.media]


def _sniff(data: bytes, kind: str, ext: str) -> bool:
    """Check that the bytes really are the declared type."""
    if kind == "image":
        from PIL import Image

        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            return True
        except Exception:
            return False
    if ext == "mp4":
        return len(data) > 12 and data[4:8] == b"ftyp"
    return data[:4] == b"\x1a\x45\xdf\xa3"  # WebM / Matroska EBML header


def _remove(media: ModelMedia) -> None:
    from services.r2_mirror import mirror_delete

    try:
        os.remove(os.path.join(media_folder(media.model_id), media.filename))
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Could not remove media file %s", media.filename, exc_info=True)
    mirror_delete(media_r2_key(media))


@model_media_bp.route("/models/<model_id>/media", methods=["GET"])
@login_required
@require_model_editor
def list_media(model_id):
    model = db.session.get(Model3D, model_id)
    return jsonify({"media": model_media_list(model)})


@model_media_bp.route("/models/<model_id>/media", methods=["POST"])
@login_required
@require_model_editor
def upload_media(model_id):
    from services.r2_mirror import mirror_file

    model = db.session.get(Model3D, model_id)
    upload = request.files.get("file")
    mimetype = (upload.mimetype or "").split(";")[0].strip().lower() if upload else ""
    if not upload or mimetype not in _TYPES:
        return jsonify({"ok": False, "error": "Upload a PNG, JPEG or WebP image or an MP4 or WebM video."}), 400
    kind, ext = _TYPES[mimetype]
    limit = MAX_IMAGE_BYTES if kind == "image" else MAX_VIDEO_BYTES
    data = upload.read(limit + 1)
    if len(data) > limit:
        return jsonify({"ok": False, "error": f"This {kind} is larger than {limit // (1024 * 1024)} MB."}), 400
    if not _sniff(data, kind, ext):
        return jsonify({"ok": False, "error": f"This file is not a valid {kind}."}), 400
    source = "auto" if request.form.get("source") == "auto" else "user"
    label = (request.form.get("label") or "").strip()[:LABEL_MAX] or None

    replaced = []
    if source == "auto" and label:
        # Generated media: a new run replaces the previous file of the same name.
        replaced = ModelMedia.query.filter_by(model_id=model.id, source="auto", kind=kind, label=label).all()
    count = ModelMedia.query.filter_by(model_id=model.id, kind=kind).count() - len(replaced)
    cap = MAX_IMAGES_PER_MODEL if kind == "image" else MAX_VIDEOS_PER_MODEL
    if count >= cap:
        return jsonify({"ok": False, "error": f"A model keeps at most {cap} saved {kind}s. Delete some first."}), 400

    folder = media_folder(model.id)
    os.makedirs(folder, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(folder, filename)
    with open(path, "wb") as handle:
        handle.write(data)
    media = ModelMedia(
        model_id=model.id, kind=kind, source=source, label=label,
        filename=filename, mimetype=mimetype, file_size=len(data),
    )
    try:
        for old in replaced:
            db.session.delete(old)
        db.session.add(media)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        os.remove(path)
        logger.exception("Could not save media for model %s", model.id)
        return jsonify({"ok": False, "error": "The file could not be saved. Please try again."}), 500
    for old in replaced:
        _remove(old)
    mirror_file(path, media_r2_key(media))
    return jsonify({"ok": True, "media": media_to_dict(media)}), 201


@model_media_bp.route("/models/<model_id>/media/<int:media_id>/delete", methods=["POST"])
@login_required
@require_model_editor
def delete_media(model_id, media_id):
    media = db.session.get(ModelMedia, media_id)
    if not media or media.model_id != model_id:
        abort(404)
    try:
        db.session.delete(media)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"ok": False, "error": "The file could not be deleted."}), 500
    _remove(media)
    return jsonify({"ok": True})


@model_media_bp.route("/files/<model_id>/media/<int:media_id>")
@login_required
@require_model_editor
def serve_media(model_id, media_id):
    from services.r2_mirror import ensure_local

    media = db.session.get(ModelMedia, media_id)
    if not media or media.model_id != model_id:
        abort(404)
    folder = media_folder(model_id)
    ensure_local(os.path.join(folder, media.filename), media_r2_key(media))
    if not os.path.exists(os.path.join(folder, media.filename)):
        abort(404)
    download = request.args.get("download") == "1"
    name = f"{(media.label or media.kind).replace('/', '-')}.{media.filename.rsplit('.', 1)[1]}"
    response = send_from_directory(
        folder, media.filename, mimetype=media.mimetype, conditional=True,
        as_attachment=download, download_name=name,
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@model_media_bp.app_context_processor
def _inject_media_helpers():
    return {"model_media_list": model_media_list}
