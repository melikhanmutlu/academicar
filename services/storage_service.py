import errno
import os
import shutil
from werkzeug.utils import secure_filename
import logging

logger = logging.getLogger(__name__)

STORAGE_FULL_MESSAGE = "Storage is temporarily full, so this upload could not be saved. Please try again later."


class StorageError(Exception):
    """Raised when a file system operation fails due to permissions, disk space, etc."""
    pass


class StorageFullError(StorageError):
    """The volume is out of space (ENOSPC / quota); the message is safe to show."""


def is_out_of_space_error(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT)


def upload_space_shortfall(folder: str, request_bytes: int | None, *, medical: bool, min_free: int) -> dict | None:
    """None when the volume holding ``folder`` can take an upload of
    ``request_bytes``; otherwise the numbers for the log line. Needs
    ``request_bytes`` x factor + ``min_free`` free, with factor 4 for medical
    scans (raw file + extraction) and 2 otherwise (source + archived copy).
    ``request_bytes`` None (no Content-Length) enforces only the reserve."""
    probe = folder
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        free = shutil.disk_usage(probe or ".").free
    except OSError:
        return None  # cannot tell; the ENOSPC handler is the backstop
    factor = 4 if medical else 2
    required = int(request_bytes or 0) * factor + int(min_free)
    if free >= required:
        return None
    return {"free": free, "required": required, "request_bytes": int(request_bytes or 0), "factor": factor, "min_free": int(min_free)}


def safe_save_file(file_obj, destination_path: str) -> None:
    """Safely save a Werkzeug FileStorage object to the destination."""
    try:
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        file_obj.save(destination_path)
    except OSError as e:
        logger.error(f"Failed to save file {destination_path}: {e}")
        if is_out_of_space_error(e):
            raise StorageFullError(STORAGE_FULL_MESSAGE) from e
        raise StorageError("Storage unavailable or full. Please try again later.") from e

def safe_move_file(source_path: str, destination_path: str) -> None:
    """Safely move a file across the file system."""
    try:
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        shutil.move(source_path, destination_path)
    except OSError as e:
        logger.error(f"Failed to move file from {source_path} to {destination_path}: {e}")
        raise StorageError("Storage operation failed. Please try again later.") from e

def save_companion_files(file_list, upload_dir: str, allowed_extensions: set) -> list[str]:
    """Safely save uploaded companion files (MTL, textures) to upload_dir."""
    saved: list[str] = []
    for f in file_list:
        if not f or not f.filename:
            continue
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in allowed_extensions:
            continue
        name = secure_filename(f.filename)
        if not name:
            continue
        path = os.path.join(upload_dir, name)
        safe_save_file(f, path)
        saved.append(path)
    return saved
