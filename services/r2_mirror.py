"""Best-effort Cloudflare R2 mirror.

Uploads local files to an S3-compatible R2 bucket after each write.
If R2 env vars are not set, every call is a silent no-op.
Mirror failures never block the caller.
"""

import logging
import os
import threading
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_client():
    account_id = os.environ.get("R2_ACCOUNT_ID")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    if not all([account_id, access_key, secret_key]):
        return None
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="auto",
    )


def _is_enabled() -> bool:
    return _get_client() is not None


def _bucket() -> str:
    return os.environ.get("R2_BUCKET_NAME", "academicar-backup")


def _upload_sync(local_path: str, r2_key: str) -> None:
    try:
        client = _get_client()
        if client is None:
            return
        client.upload_file(local_path, _bucket(), r2_key)
        logger.info("R2 mirror: %s -> %s", r2_key, _bucket())
    except Exception:
        logger.warning("R2 mirror failed: %s", r2_key, exc_info=True)


def mirror_file(local_path: str, r2_key: str) -> None:
    if not _is_enabled() or not os.path.isfile(local_path):
        return
    t = threading.Thread(target=_upload_sync, args=(local_path, r2_key), daemon=True)
    t.start()


def mirror_directory(local_dir: str, r2_prefix: str) -> None:
    if not _is_enabled() or not os.path.isdir(local_dir):
        return
    for root, _, files in os.walk(local_dir):
        for fname in files:
            file_path = os.path.join(root, fname)
            rel = os.path.relpath(file_path, local_dir)
            r2_key = f"{r2_prefix}/{rel}".replace("\\", "/")
            mirror_file(file_path, r2_key)


def mirror_delete(r2_key: str) -> None:
    if not _is_enabled():
        return

    def _delete():
        try:
            client = _get_client()
            if client:
                client.delete_object(Bucket=_bucket(), Key=r2_key)
                logger.info("R2 mirror deleted: %s", r2_key)
        except Exception:
            logger.warning("R2 mirror delete failed: %s", r2_key, exc_info=True)

    t = threading.Thread(target=_delete, daemon=True)
    t.start()


def restore_file(local_path: str, r2_key: str) -> bool:
    """Download ``r2_key`` from R2 to ``local_path`` (atomic via a temp file).

    Synchronous on purpose: callers need the bytes present before serving.
    Returns True on success, False if R2 is disabled or the object is missing.
    """
    if not _is_enabled():
        return False
    client = _get_client()
    if client is None:
        return False
    tmp = f"{local_path}.r2restore"
    try:
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        client.download_file(_bucket(), r2_key, tmp)
        os.replace(tmp, local_path)
        logger.info("R2 restore: %s <- %s", local_path, r2_key)
        return True
    except Exception:
        logger.warning("R2 restore failed: %s", r2_key, exc_info=True)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def ensure_local(local_path: str, r2_key: str) -> bool:
    """Make sure ``local_path`` exists, restoring it from the R2 mirror when the
    local copy is missing (e.g. after an ephemeral Railway volume is recycled).

    Returns True if the file is present locally afterwards. A no-op that returns
    True when the file already exists, and False when R2 is unavailable.
    """
    if os.path.isfile(local_path):
        return True
    return restore_file(local_path, r2_key)

