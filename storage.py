"""Storage provider abstraction.

The MVP starts with local/Railway volume paths, but application code should
avoid assuming that files will always live on the same disk forever.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredFile:
    key: str
    path: str
    size_bytes: int


class StorageProvider:
    def save_file(self, source_path: str, key: str) -> StoredFile:
        raise NotImplementedError

    def read_file(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def get_serving_url(self, key: str) -> str:
        raise NotImplementedError

    def path_for(self, key: str) -> str:
        raise NotImplementedError

    def migrate_file(self, source_provider: "StorageProvider", source_key: str, target_key: str) -> StoredFile:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


class LocalStorageProvider(StorageProvider):
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> str:
        safe_key = key.replace("\\", "/").lstrip("/")
        return str((self.root / safe_key).resolve())

    def save_file(self, source_path: str, key: str) -> StoredFile:
        target = Path(self.path_for(key))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        return StoredFile(key=key, path=str(target), size_bytes=target.stat().st_size)

    def read_file(self, key: str) -> bytes:
        return Path(self.path_for(key)).read_bytes()

    def exists(self, key: str) -> bool:
        return Path(self.path_for(key)).exists()

    def get_serving_url(self, key: str) -> str:
        return self.path_for(key)

    def migrate_file(self, source_provider: StorageProvider, source_key: str, target_key: str) -> StoredFile:
        target = Path(self.path_for(target_key))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source_provider.read_file(source_key))
        return StoredFile(key=target_key, path=str(target), size_bytes=target.stat().st_size)

    def delete(self, key: str) -> None:
        path = Path(self.path_for(key))
        if path.exists():
            path.unlink()


def build_storage_provider(provider: str | None, root: str) -> StorageProvider:
    normalized = (provider or "local").strip().lower()
    if normalized in {"local", "railway_volume"}:
        return LocalStorageProvider(root)
    raise ValueError(f"Unsupported storage provider: {provider}")
