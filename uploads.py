"""Resumable chunked uploads for model files.

A browser sends the file in small PUT requests instead of one huge multipart
POST, so a dropped connection costs one chunk (retried or resumed) instead of
the whole upload, and the page can show real server-confirmed progress. A
truncated multipart body is also what made Werkzeug parse an empty form and
Flask-WTF report "The CSRF token is missing."

Flow:  POST /uploads  ->  PUT /uploads/<id>?offset=N (repeat)  ->  the normal
form (upload_model / model_replace / paper_new) is submitted with a hidden
``upload_id`` instead of the file. The route then adopts the staged file through
``resolve_staged_upload`` and runs the exact same validation, consent, medical
and limit checks as for a real file.

Staged files live outside UPLOAD_FOLDER, so backups never contain them:
medical inputs under MEDICAL_STAGING_FOLDER (their name is never stored),
everything else under UPLOAD_STAGING_FOLDER. ``sweep_orphaned_temp_artifacts``
removes anything left behind once it is old enough; a cancelled, failed or
consumed upload is removed at once.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from flask import Blueprint, after_this_request, current_app, jsonify, request
from flask_login import current_user
from flask_wtf.csrf import generate_csrf
from werkzeug.utils import secure_filename

from extensions import limiter
from models import Model3D, Paper, db

logger = logging.getLogger(__name__)

uploads_bp = Blueprint("uploads", __name__)

CHUNK_SIZE = 8 * 1024 * 1024
MAX_ACTIVE_UPLOADS_PER_USER = 5
# An upload nobody touched for this long no longer counts against the per-user cap.
IDLE_SESSION_SECONDS = 3600
_LOCK_STALE_SECONDS = 60
_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_DATA_NAME = "data.bin"
_META_NAME = "meta.json"
_LOCK_NAME = ".lock"

INCOMPLETE_MESSAGE = "The upload is not complete. Please select the file and upload it again."
UNKNOWN_UPLOAD_MESSAGE = "This upload is no longer available. Please select the file and upload it again."


def _error(message: str, status: int, **extra):
    return jsonify({"error": message, **extra}), status


def _staging_root(medical: bool) -> str:
    return current_app.config["MEDICAL_STAGING_FOLDER" if medical else "UPLOAD_STAGING_FOLDER"]


def _find_dir(upload_id: str) -> str | None:
    if not _ID_PATTERN.fullmatch(upload_id or ""):
        return None
    for medical in (False, True):
        directory = os.path.join(_staging_root(medical), upload_id)
        if os.path.isfile(os.path.join(directory, _META_NAME)):
            return directory
    return None


def _read_meta(directory: str) -> dict | None:
    try:
        with open(os.path.join(directory, _META_NAME), encoding="utf-8") as fh:
            meta = json.load(fh)
        return meta if isinstance(meta, dict) else None
    except (OSError, ValueError):
        return None


def _received(directory: str) -> int:
    try:
        return os.path.getsize(os.path.join(directory, _DATA_NAME))
    except OSError:
        return 0


def _remove_dir(directory: str | None) -> None:
    if directory:
        shutil.rmtree(directory, ignore_errors=True)


class UploadBusy(Exception):
    """Another request is writing this upload right now."""


@contextmanager
def _upload_lock(directory: str):
    """Cross-thread / cross-process lock: mkdir is atomic everywhere. A lock
    older than _LOCK_STALE_SECONDS belongs to a request that died."""
    lock = os.path.join(directory, _LOCK_NAME)
    for attempt in range(2):
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            try:
                stale = time.time() - os.path.getmtime(lock) > _LOCK_STALE_SECONDS
            except OSError:
                stale = True
            if attempt == 0 and stale:
                shutil.rmtree(lock, ignore_errors=True)
                continue
            raise UploadBusy()
    try:
        yield
    finally:
        shutil.rmtree(lock, ignore_errors=True)


def _space_shortfall(directory_root: str, size: int, *, medical: bool, received: int):
    from services.storage_service import upload_space_shortfall

    shortfall = upload_space_shortfall(
        directory_root,
        size,
        medical=medical,
        min_free=int(current_app.config.get("STORAGE_MIN_FREE_BYTES") or 0),
        already_written=received,
    )
    if shortfall is not None:
        logger.error(
            "Storage low: refusing chunked upload (free=%d bytes, required=%d, declared %d, staged %d)",
            shortfall["free"], shortfall["required"], size, received,
        )
    return shortfall


def _json_login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return _error("Please log in again to continue the upload.", 401, code="login_required")
        return view(*args, **kwargs)

    return wrapper


def _owned_upload(upload_id: str):
    """(directory, meta) for an upload of the current user, else (None, None).
    Other people's uploads look exactly like missing ones."""
    directory = _find_dir(upload_id)
    meta = _read_meta(directory) if directory else None
    if not meta or meta.get("user_id") != current_user.id:
        return None, None
    return directory, meta


def _cap_reached(user_id: int) -> bool:
    """Drop this user's long-idle staged uploads, then say whether five are still open."""
    open_count = 0
    now = time.time()
    for medical in (False, True):
        root = _staging_root(medical)
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            if not _ID_PATTERN.fullmatch(name):
                continue
            directory = os.path.join(root, name)
            meta = _read_meta(directory)
            if not meta or meta.get("user_id") != user_id:
                continue
            try:
                idle = now - max(os.path.getmtime(directory), os.path.getmtime(os.path.join(directory, _DATA_NAME)))
            except OSError:
                idle = IDLE_SESSION_SECONDS + 1
            if idle > IDLE_SESSION_SECONDS:
                _remove_dir(directory)
            else:
                open_count += 1
    return open_count >= MAX_ACTIVE_UPLOADS_PER_USER


def _resolve_target(target) -> tuple[dict | None, int | None, tuple | None]:
    """(stored target, owning user id, error response) for a request target.

    The owner is who the model will belong to (the project owner), which is
    who funds it and whose plan limits apply."""
    from app import active_paper_query
    from collaborators import can_edit_project

    if not isinstance(target, dict):
        return None, None, _error("Missing upload target.", 400)
    if target.get("new_project") is True:
        return {"type": "new_project", "key": None}, current_user.id, None
    slug = target.get("paper_slug")
    if isinstance(slug, str) and slug:
        paper = active_paper_query().filter_by(slug=slug).first()
        if paper is None:
            return None, None, _error("Project not found.", 404)
        if not can_edit_project(paper):
            return None, None, _error("You do not have permission to add models to this project.", 403)
        return {"type": "paper", "key": slug}, paper.user_id, None
    model_id = target.get("model_id")
    if isinstance(model_id, str) and model_id:
        model = db.session.get(Model3D, model_id)
        if model is None or model.paper is None:
            return None, None, _error("Model not found.", 404)
        if model.user_id != current_user.id and not can_edit_project(model.paper):
            return None, None, _error("You do not have permission to replace this model.", 403)
        return {"type": "model", "key": model_id}, model.user_id, None
    return None, None, _error("Missing upload target.", 400)


def _limit_error(target: dict, owner_user_id: int, size: int, medical: bool) -> str | None:
    """The message the final upload would give for this declared size, or None."""
    import app as app_module
    from institutions import get_active_membership, institution_can_fund_upload
    from licensing import model_file_limit_error

    if medical:
        limit = app_module.MEDICAL_UPLOAD_MAX_BYTES
        if limit and size > limit:
            return (
                f"This scan is {size // (1024 * 1024)} MB; the limit is {limit // (1024 * 1024)} MB. "
                "Upload a thicker-slice series or crop it to the region you need."
            )
        return None
    if target["type"] == "model":
        return model_file_limit_error(size, db.session.get(Model3D, target["key"]).license_type)
    plan = "free"
    membership = get_active_membership(owner_user_id)
    if membership is not None and institution_can_fund_upload(membership.institution, size)[0]:
        plan = "institutional"
    if target["type"] == "paper":
        paper = app_module.active_paper_query().filter_by(slug=target["key"]).first()
        capacity = app_module.project_model_capacity(paper, intended_plan=plan)
        if not capacity["can_add"]:
            return (
                f"The {capacity['plan'].label} plan allows {capacity['limit']} model(s) "
                "per project. Upgrade the existing model to add more, or start a new project."
            )
    return model_file_limit_error(size, plan)


@uploads_bp.route("/uploads", methods=["POST"])
@_json_login_required
@limiter.limit("60 per hour", methods=["POST"])
def create_upload():
    import app as app_module

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error("Invalid request.", 400)
    filename = payload.get("filename")
    size = payload.get("size")
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
        return _error("Missing file name.", 400)
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return _error("The file is empty.", 400)
    if not app_module.allowed_model(filename):
        return _error(app_module.ALLOWED_MODEL_FILES_MESSAGE, 400)

    target, owner_user_id, failure = _resolve_target(payload.get("target"))
    if failure:
        return failure
    ext = app_module.model_upload_extension(filename)
    medical = ext in app_module.MEDICAL_MODEL_EXTENSIONS
    limit_message = _limit_error(target, owner_user_id, size, medical)
    if limit_message:
        return _error(limit_message, 413)

    root = _staging_root(medical)
    if _space_shortfall(root, size, medical=medical, received=0) is not None:
        from services.storage_service import STORAGE_FULL_MESSAGE

        return _error(STORAGE_FULL_MESSAGE, 507)
    if _cap_reached(current_user.id):
        return _error("You already have several unfinished uploads. Finish or cancel one first.", 429)

    upload_id = uuid.uuid4().hex
    directory = os.path.join(root, upload_id)
    # A medical scan's file name may carry patient data: only a neutral name is kept.
    stored_name = app_module.medical_neutral_name(secure_filename(filename)) if medical else secure_filename(filename)
    meta = {
        "user_id": current_user.id,
        "target": target,
        "ext": ext,
        "filename": stored_name,
        "size": size,
        "medical": medical,
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        os.makedirs(directory)
        with open(os.path.join(directory, _DATA_NAME), "wb"):
            pass
        with open(os.path.join(directory, _META_NAME), "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
    except OSError as exc:
        _remove_dir(directory)
        from services.storage_service import STORAGE_FULL_MESSAGE, is_out_of_space_error

        if is_out_of_space_error(exc):
            return _error(STORAGE_FULL_MESSAGE, 507)
        logger.exception("Could not start a chunked upload")
        return _error("The upload could not be started. Please try again.", 500)
    return jsonify(
        {"upload_id": upload_id, "chunk_size": CHUNK_SIZE, "received": 0, "size": size, "csrf_token": generate_csrf()}
    ), 201


@uploads_bp.route("/uploads/<upload_id>", methods=["GET"])
@_json_login_required
def upload_status(upload_id):
    directory, meta = _owned_upload(upload_id)
    if not directory:
        return _error(UNKNOWN_UPLOAD_MESSAGE, 404)
    return jsonify(
        {"received": _received(directory), "size": meta["size"], "chunk_size": CHUNK_SIZE, "csrf_token": generate_csrf()}
    )


@uploads_bp.route("/uploads/<upload_id>", methods=["PUT"])
@_json_login_required
@limiter.limit("3000 per hour", methods=["PUT"])
def upload_chunk(upload_id):
    directory, meta = _owned_upload(upload_id)
    if not directory:
        return _error(UNKNOWN_UPLOAD_MESSAGE, 404)
    offset = request.args.get("offset", type=int)
    if offset is None or offset < 0:
        return _error("Missing or invalid offset.", 400)
    length = request.content_length
    if length is None:
        return _error("Missing Content-Length.", 411)
    if length > CHUNK_SIZE:
        return _error("This chunk is too large.", 413)
    body = request.get_data(cache=False)  # a cut-off body raises before we write anything
    if len(body) != length:
        return _error("The chunk was cut off. Retrying.", 400)

    size = meta["size"]
    data_path = os.path.join(directory, _DATA_NAME)
    try:
        with _upload_lock(directory):
            current = _received(directory)
            if offset > current:
                return _error("The chunk does not follow the data received so far.", 409, received=current)
            overlap = min(len(body), current - offset)
            if overlap:
                # A retry of a chunk whose reply was lost: the bytes must be the ones already stored.
                with open(data_path, "rb") as fh:
                    fh.seek(offset)
                    if fh.read(overlap) != body[:overlap]:
                        return _error("The chunk does not match the data received so far.", 409, received=current)
            fresh = body[overlap:]
            if current + len(fresh) > size:
                return _error("More data than the declared file size.", 413, received=current)
            if fresh:
                if _space_shortfall(os.path.dirname(directory), size, medical=meta["medical"], received=current):
                    from services.storage_service import STORAGE_FULL_MESSAGE

                    return _error(STORAGE_FULL_MESSAGE, 507, received=current)
                try:
                    with open(data_path, "r+b") as fh:
                        fh.seek(current)
                        fh.write(fresh)
                        fh.flush()
                except OSError as exc:
                    try:  # drop a partly written chunk so the stored size stays truthful
                        with open(data_path, "r+b") as fh:
                            fh.truncate(current)
                    except OSError:
                        pass
                    from services.storage_service import STORAGE_FULL_MESSAGE, is_out_of_space_error

                    if is_out_of_space_error(exc):
                        return _error(STORAGE_FULL_MESSAGE, 507, received=current)
                    logger.exception("Could not store an upload chunk")
                    return _error("The chunk could not be saved. Retrying.", 500, received=current)
            received = current + len(fresh)
    except UploadBusy:
        return _error("Another chunk of this upload is still being saved.", 503)
    return jsonify({"received": received, "size": size, "csrf_token": generate_csrf()})


@uploads_bp.route("/uploads/<upload_id>", methods=["DELETE"])
@uploads_bp.route("/uploads/<upload_id>/cancel", methods=["POST"])
@_json_login_required
def cancel_upload(upload_id):
    directory, _meta = _owned_upload(upload_id)
    if directory:
        _remove_dir(directory)
    return jsonify({"ok": True})


class StagedUpload:
    """A finished chunked upload, shaped like a Werkzeug FileStorage for the
    upload pipeline: ``filename`` and ``save(destination)``. ``save`` moves the
    file into place (no copy) and removes the staging folder."""

    def __init__(self, directory: str, meta: dict):
        self.directory = directory
        self.filename = meta["filename"]
        self.size = int(meta["size"])
        self.staged_bytes = self.size  # already on disk: the finishing request carries none
        self.path = os.path.join(directory, _DATA_NAME)

    def save(self, destination: str) -> None:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        try:
            os.replace(self.path, destination)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            shutil.move(self.path, destination)  # staging on another volume: a copy is unavoidable
        self.discard()

    def discard(self) -> None:
        _remove_dir(self.directory)


def resolve_staged_upload(target_type: str, target_key: str | None):
    """Adopt the completed chunked upload named by the form's ``upload_id``.

    Returns ``(staged, error)``: ``(None, None)`` when the form carries no
    ``upload_id`` (a real file upload), ``(None, message)`` when the id is
    unusable (unknown, someone else's, for another target, not complete), and
    ``(StagedUpload, None)`` otherwise. The staged file is removed after the
    request whatever happens, so a rejected upload leaves nothing behind."""
    upload_id = (request.form.get("upload_id") or "").strip()
    if not upload_id:
        return None, None
    directory = _find_dir(upload_id)
    meta = _read_meta(directory) if directory else None
    if not meta or meta.get("user_id") != getattr(current_user, "id", None):
        return None, UNKNOWN_UPLOAD_MESSAGE
    target = meta.get("target") or {}
    if target.get("type") != target_type or target.get("key") != target_key:
        return None, UNKNOWN_UPLOAD_MESSAGE
    staged = StagedUpload(directory, meta)
    after_this_request(lambda response: (staged.discard(), response)[1])
    if _received(directory) != staged.size:
        return None, INCOMPLETE_MESSAGE
    return staged, None
