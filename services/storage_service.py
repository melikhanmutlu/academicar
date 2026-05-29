import os
import shutil
from werkzeug.utils import secure_filename
import logging

logger = logging.getLogger(__name__)

class StorageError(Exception):
    """Raised when a file system operation fails due to permissions, disk space, etc."""
    pass

def safe_save_file(file_obj, destination_path: str) -> None:
    """Safely save a Werkzeug FileStorage object to the destination."""
    try:
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        file_obj.save(destination_path)
    except OSError as e:
        logger.error(f"Failed to save file {destination_path}: {e}")
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
