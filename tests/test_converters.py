from pathlib import Path
import os
import shutil
import subprocess

import pytest
import trimesh

from converters.external_converter import FBXConverter, OBJConverter
from converters.glb_quality import GLBQualityError, ensure_pbr_materials, validate_glb_quality


def temp_converter_dir() -> Path:
    path = Path.cwd() / f".pytest-converters-{os.getpid()}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir()
    return path


def test_obj_converter_reports_cli_failure(monkeypatch):
    tmp_dir = temp_converter_dir()
    source = tmp_dir / "mesh.obj"
    output = tmp_dir / "mesh.glb"
    source.write_text("o mesh\n", encoding="utf-8")

    try:
        def fake_run(command, cwd):
            return subprocess.CompletedProcess(command, 1, "", "obj2gltf failed")

        converter = OBJConverter()
        monkeypatch.setattr(converter, "_run", fake_run)

        assert converter.convert(str(source), str(output)) is False
        assert "obj2gltf failed" in converter.errors
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_fbx_converter_accepts_generated_glb_candidate(monkeypatch):
    tmp_dir = temp_converter_dir()
    source = tmp_dir / "mesh.fbx"
    source.write_bytes(b"fbx")
    output = tmp_dir / "target.glb"
    generated = tmp_dir / "mesh.glb"

    try:
        def fake_run(command, cwd):
            generated.write_bytes(b"glb")
            return subprocess.CompletedProcess(command, 0, "", "")

        converter = FBXConverter()
        monkeypatch.setattr(converter, "_run", fake_run)

        assert converter.convert(str(source), str(output), color="#nothex") is True
        assert output.read_bytes() == b"glb"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_glb_quality_rejects_invalid_output():
    tmp_dir = temp_converter_dir()
    bad_glb = tmp_dir / "bad.glb"
    bad_glb.write_bytes(b"not-a-glb")

    try:
        with pytest.raises(GLBQualityError):
            validate_glb_quality(str(bad_glb))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_glb_quality_adds_missing_pbr_materials():
    tmp_dir = temp_converter_dir()
    glb_path = tmp_dir / "box.glb"

    try:
        trimesh.creation.box().export(glb_path)
        ensure_pbr_materials(str(glb_path))
        validate_glb_quality(str(glb_path))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
