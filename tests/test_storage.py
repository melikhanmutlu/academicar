from pathlib import Path
import os
import shutil

from storage import LocalStorageProvider


def test_local_storage_provider_can_save_read_and_migrate():
    tmp_root = Path.cwd() / f".pytest-storage-{os.getpid()}"
    shutil.rmtree(tmp_root, ignore_errors=True)
    tmp_root.mkdir()
    source_root = tmp_root / "source"
    target_root = tmp_root / "target"
    source_provider = LocalStorageProvider(str(source_root))
    target_provider = LocalStorageProvider(str(target_root))

    try:
        source_file = tmp_root / "input.glb"
        source_file.write_bytes(b"glb-data")

        stored = source_provider.save_file(str(source_file), "models/demo/model.glb")
        assert stored.key == "models/demo/model.glb"
        assert stored.size_bytes == len(b"glb-data")
        assert source_provider.exists(stored.key)
        assert source_provider.read_file(stored.key) == b"glb-data"
        assert Path(source_provider.get_serving_url(stored.key)).exists()

        migrated = target_provider.migrate_file(source_provider, stored.key, "archive/model.glb")
        assert migrated.key == "archive/model.glb"
        assert target_provider.read_file(migrated.key) == b"glb-data"

        source_provider.delete(stored.key)
        assert not source_provider.exists(stored.key)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
