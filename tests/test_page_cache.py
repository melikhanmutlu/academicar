"""Files the backup reads or writes, and files mirrored to R2, are dropped
from the kernel page cache afterwards. Railway counts a service's page cache
as its memory, so the daily backup (which reads every stored file) otherwise
left the service at ~3.4 GB "in use" until the next restart."""
import os
import zipfile

from utils import page_cache


def test_drop_page_cache_tolerates_missing_files(tmp_path):
    page_cache.drop_page_cache(str(tmp_path / "missing.bin"))
    page_cache.drop_page_cache(str(tmp_path / "missing.bin"), flush=True)


def test_drop_page_cache_asks_the_kernel_to_forget_the_file(tmp_path, monkeypatch):
    target = tmp_path / "model.glb"
    target.write_bytes(b"x" * 4096)
    calls = []
    monkeypatch.setattr(os, "posix_fadvise", lambda fd, offset, length, advice: calls.append((offset, length, advice)))
    page_cache.drop_page_cache(str(target), flush=True)
    assert calls == [(0, 0, os.POSIX_FADV_DONTNEED)]


def test_backup_zip_drops_each_source_file_from_cache(tmp_path, monkeypatch):
    from app import add_folder_to_zip

    folder = tmp_path / "converted"
    (folder / "m1").mkdir(parents=True)
    (folder / "m1" / "model.glb").write_bytes(b"glb")
    (folder / "m1" / "model.usdz").write_bytes(b"usdz")
    dropped = []
    monkeypatch.setattr("app.drop_page_cache", lambda path, **kw: dropped.append(path))
    with zipfile.ZipFile(tmp_path / "out.zip", "w") as zf:
        assert add_folder_to_zip(zf, str(folder), "converted") == 2
    assert sorted(os.path.basename(p) for p in dropped) == ["model.glb", "model.usdz"]


def test_backup_archive_is_dropped_from_cache(app, tmp_path, monkeypatch):
    from app import create_backup_archive

    monkeypatch.setitem(app.config, "STORAGE_ROOT", str(tmp_path))
    dropped = []
    monkeypatch.setattr("app.drop_page_cache", lambda path, **kw: dropped.append((os.path.basename(path), kw.get("flush", False))))
    with app.app_context():
        filename = create_backup_archive(app, reason="test")
    assert (filename, True) in dropped


def test_r2_upload_drops_the_uploaded_file_from_cache(tmp_path, monkeypatch):
    from services import r2_mirror

    target = tmp_path / "archive.zip"
    target.write_bytes(b"zip")

    class FakeClient:
        def upload_file(self, *args):
            pass

    dropped = []
    monkeypatch.setattr(r2_mirror, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(r2_mirror, "drop_page_cache", lambda path, **kw: dropped.append(path))
    assert r2_mirror._upload_sync(str(target), "admin_backups/archive.zip") is True
    assert dropped == [str(target)]
