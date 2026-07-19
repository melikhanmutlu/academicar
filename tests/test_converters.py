from pathlib import Path
import os
import shutil
import subprocess

import pytest
import trimesh

from converters.external_converter import FBXConverter, OBJConverter
from converters.glb_quality import (
    GLBQualityError,
    ensure_pbr_materials,
    repair_transparent_base_color,
    validate_glb_quality,
)


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


# A tetrahedron: 4 triangles = 12 unwelded corner vertices, but only 4 UNIQUE
# vertices. Exercises the STL complexity check, which must count unique (welded)
# vertices, not the 3x-inflated unwelded representation the loader produces.
_TETRA_STL = b"""solid tetra
facet normal 0 0 1
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 0 1 0
 endloop
endfacet
facet normal 0 1 0
 outer loop
  vertex 0 0 0
  vertex 0 0 1
  vertex 1 0 0
 endloop
endfacet
facet normal 1 0 0
 outer loop
  vertex 0 0 0
  vertex 0 1 0
  vertex 0 0 1
 endloop
endfacet
facet normal 1 1 1
 outer loop
  vertex 1 0 0
  vertex 0 0 1
  vertex 0 1 0
 endloop
endfacet
endsolid tetra
"""


def test_stl_vertex_limit_counts_unique_not_unwelded(monkeypatch):
    """The tetra has 12 unwelded corner vertices but only 4 unique ones. A vertex
    limit of 8 (below 12, above 4) must NOT reject it: the check counts unique
    (welded) vertices so flat-shaded STLs aren't rejected at ~1/3 their real cap."""
    from converters.stl_converter import STLConverter

    monkeypatch.setattr("converters.stl_converter.MAX_MESH_VERTICES", 8)
    monkeypatch.setattr("converters.stl_converter.MAX_MESH_FACES", 100)
    tmp_dir = temp_converter_dir()
    source = tmp_dir / "tetra.stl"
    source.write_bytes(_TETRA_STL)
    output = tmp_dir / "tetra.glb"
    try:
        converter = STLConverter()
        assert converter.convert(str(source), str(output)) is True
        assert output.exists()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_stl_rejects_when_triangle_limit_exceeded(monkeypatch):
    """The triangle limit still guards worker memory: a 4-triangle mesh is
    rejected when the cap is 2."""
    from converters.stl_converter import STLConverter

    monkeypatch.setattr("converters.stl_converter.MAX_MESH_FACES", 2)
    tmp_dir = temp_converter_dir()
    source = tmp_dir / "tetra.stl"
    source.write_bytes(_TETRA_STL)
    output = tmp_dir / "tetra.glb"
    try:
        converter = STLConverter()
        assert converter.convert(str(source), str(output)) is False
        assert any("too complex" in e for e in converter.errors)
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


def test_repair_transparent_base_color_restores_invisible_textured_material():
    """FBX2glTF can emit a textured material with baseColorFactor alpha 0.0
    (fully transparent → invisible). The repair restores alpha to 1.0 so the
    texture renders, e.g. tree foliage."""
    from pygltflib import GLTF2, TextureInfo

    tmp_dir = temp_converter_dir()
    glb_path = tmp_dir / "leaf.glb"

    try:
        trimesh.creation.box().export(glb_path)
        gltf = GLTF2.load(str(glb_path))
        pbr = gltf.materials[0].pbrMetallicRoughness
        # Simulate FBX2glTF's broken opacity mapping on a textured material.
        pbr.baseColorTexture = TextureInfo(index=0)
        pbr.baseColorFactor = [0.8, 0.8, 0.8, 0.0]
        gltf.materials[0].alphaMode = "BLEND"
        gltf.save(str(glb_path))

        assert repair_transparent_base_color(str(glb_path)) is True

        fixed = GLTF2.load(str(glb_path))
        assert fixed.materials[0].pbrMetallicRoughness.baseColorFactor[3] == 1.0
        # alphaMode is preserved so per-texel texture alpha still drives cutouts.
        assert fixed.materials[0].alphaMode == "BLEND"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_repair_transparent_base_color_leaves_untextured_transparent_material():
    """A transparent material with no baseColorTexture is left untouched: the
    repair only targets textured-but-invisible materials."""
    from pygltflib import GLTF2

    tmp_dir = temp_converter_dir()
    glb_path = tmp_dir / "glass.glb"

    try:
        trimesh.creation.box().export(glb_path)
        gltf = GLTF2.load(str(glb_path))
        gltf.materials[0].pbrMetallicRoughness.baseColorTexture = None
        gltf.materials[0].pbrMetallicRoughness.baseColorFactor = [1.0, 1.0, 1.0, 0.0]
        gltf.materials[0].alphaMode = "BLEND"
        gltf.save(str(glb_path))

        assert repair_transparent_base_color(str(glb_path)) is False

        fixed = GLTF2.load(str(glb_path))
        assert fixed.materials[0].pbrMetallicRoughness.baseColorFactor[3] == 0.0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
