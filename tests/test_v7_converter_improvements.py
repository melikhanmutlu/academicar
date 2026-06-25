"""Regression tests for v7 converter pipeline improvements.

Covers:
- gltf-transform GLB optimization integration
- Improved unit scale heuristic (µm, mm, cm, m bands + safety clamp)
- inject_pbr_material uses pygltflib instead of raw byte manipulation
- Consistent output quality across STL path
"""

import os
import shutil
import struct
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import trimesh

from converters.glb_optimize import optimize_glb
from converters.glb_quality import validate_glb_quality, ensure_pbr_materials
from converters.stl_converter import (
    STLConverter,
    enrich_glb_for_ar,
    inject_pbr_material,
    load_stl_mesh_without_normals,
)


def _tmp_dir():
    path = Path.cwd() / f".pytest-v7-{os.getpid()}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir()
    return path


def _make_test_glb(path: Path) -> str:
    """Create a minimal valid GLB for testing."""
    glb_path = str(path / "test.glb")
    mesh = trimesh.creation.box(extents=[1, 1, 1])
    trimesh.Scene([mesh]).export(glb_path)
    ensure_pbr_materials(glb_path)
    return glb_path


def _make_stl(path: Path, scale: float = 1.0) -> str:
    """Create a simple STL at the given scale."""
    stl_path = str(path / "test.stl")
    verts = np.array([[0, 0, 0], [scale, 0, 0], [0, scale, 0]], dtype=np.float64)
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
    mesh.export(stl_path)
    return stl_path


class TestOptimizeGlb:
    """gltf-transform integration."""

    def test_optimize_reduces_file_size(self):
        tmp = _tmp_dir()
        try:
            glb_path = _make_test_glb(tmp)
            original_size = os.path.getsize(glb_path)
            result = optimize_glb(glb_path)
            if result:
                optimized_size = os.path.getsize(glb_path)
                assert optimized_size <= original_size
                validate_glb_quality(glb_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_optimize_preserves_original_on_failure(self):
        tmp = _tmp_dir()
        try:
            glb_path = _make_test_glb(tmp)
            original_bytes = Path(glb_path).read_bytes()
            with patch("converters.glb_optimize._find_cli", return_value=["nonexistent-binary"]):
                result = optimize_glb(glb_path)
            assert result is False
            assert Path(glb_path).read_bytes() == original_bytes
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_optimize_missing_file_returns_false(self):
        assert optimize_glb("/nonexistent/path.glb") is False


class TestExplicitUnitScale:
    """Source unit is explicit (mm/cm/m) — no magnitude guessing, no size clamp."""

    def _convert(self, tmp, scale, unit):
        stl_path = _make_stl(tmp, scale=scale)
        glb_path = str(tmp / "output.glb")
        assert STLConverter().convert(stl_path, glb_path, source_unit=unit) is True
        mesh = trimesh.load(glb_path, force="mesh")
        return float(np.ptp(mesh.bounds, axis=0).max())

    def test_mm_unit_scales_to_meters(self):
        tmp = _tmp_dir()
        try:
            assert abs(self._convert(tmp, 200.0, "mm") - 0.2) < 0.05  # 200mm -> 0.2m
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cm_unit_scales_to_meters(self):
        tmp = _tmp_dir()
        try:
            assert abs(self._convert(tmp, 30.0, "cm") - 0.3) < 0.05  # 30cm -> 0.3m
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_m_unit_preserved(self):
        tmp = _tmp_dir()
        try:
            assert abs(self._convert(tmp, 0.5, "m") - 0.5) < 0.05  # 0.5m stays 0.5m
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_size_clamp_for_large_models(self):
        """The 2m safety clamp is gone — a 300cm model stays ~3m."""
        tmp = _tmp_dir()
        try:
            assert self._convert(tmp, 300.0, "cm") > 2.5  # 300cm -> ~3m, not clamped
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unknown_unit_left_as_authored(self):
        """No unit / unknown unit no longer guesses — mesh is left as-authored."""
        tmp = _tmp_dir()
        try:
            assert abs(self._convert(tmp, 0.4, "embedded") - 0.4) < 0.05
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestGlbHasDraco:
    """glb_has_draco gates the USDZ pipeline (never hand Draco to a Draco-less Blender)."""

    def test_plain_glb_not_draco(self):
        from converters.glb_optimize import glb_has_draco
        tmp = _tmp_dir()
        try:
            assert glb_has_draco(_make_test_glb(tmp)) is False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_draco_extension_detected(self):
        # Craft a minimal GLB whose JSON declares Draco, no CLI needed.
        import json
        from converters.glb_optimize import glb_has_draco
        tmp = _tmp_dir()
        try:
            j = json.dumps({"asset": {"version": "2.0"},
                            "extensionsUsed": ["KHR_draco_mesh_compression"]}).encode()
            j += b" " * ((4 - len(j) % 4) % 4)
            blob = b"glTF" + struct.pack("<I", 2) + struct.pack("<I", 12 + 8 + len(j))
            blob += struct.pack("<I", len(j)) + b"JSON" + j
            path = str(tmp / "draco.glb")
            with open(path, "wb") as fh:
                fh.write(blob)
            assert glb_has_draco(path) is True
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestInjectPbrMaterial:
    """inject_pbr_material now uses pygltflib, not raw bytes."""

    def test_inject_adds_material(self):
        tmp = _tmp_dir()
        try:
            glb_path = _make_test_glb(tmp)
            inject_pbr_material(glb_path, (0.5, 0.5, 0.5, 1.0))
            validate_glb_quality(glb_path)
            from pygltflib import GLTF2
            gltf = GLTF2.load(glb_path)
            found = any(
                m.name == "AcademicAR_Default"
                for m in (gltf.materials or [])
            )
            assert found, "AcademicAR_Default material should be present"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_inject_produces_valid_glb_header(self):
        """GLB output must have valid glTF header (not corrupted by byte manipulation)."""
        tmp = _tmp_dir()
        try:
            glb_path = _make_test_glb(tmp)
            inject_pbr_material(glb_path, (1.0, 0.0, 0.0, 1.0))
            with open(glb_path, "rb") as f:
                magic = f.read(4)
            assert magic == b"glTF", "GLB header must be valid after injection"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestEnrichGlbForAr:
    """enrich_glb_for_ar produces quality-validated output."""

    def test_enrich_adds_material_and_uvs(self):
        tmp = _tmp_dir()
        try:
            glb_path = str(tmp / "mesh.glb")
            mesh = trimesh.creation.box(extents=[1, 1, 1])
            trimesh.Scene([mesh]).export(glb_path)
            result = enrich_glb_for_ar(glb_path, (0.6, 0.6, 0.6, 1.0))
            assert result is True
            validate_glb_quality(glb_path)
            from pygltflib import GLTF2
            gltf = GLTF2.load(glb_path)
            for mesh_def in gltf.meshes or []:
                for prim in mesh_def.primitives or []:
                    assert getattr(prim.attributes, "TEXCOORD_0", None) is not None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestConsistentOutputQuality:
    """All format paths should produce GLBs that pass validate_glb_quality."""

    def test_stl_path_produces_valid_glb(self):
        tmp = _tmp_dir()
        try:
            stl_path = _make_stl(tmp, scale=50.0)
            glb_path = str(tmp / "output.glb")
            converter = STLConverter()
            assert converter.convert(stl_path, glb_path, color="#cc0000") is True
            validate_glb_quality(glb_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stl_default_color_produces_valid_glb(self):
        tmp = _tmp_dir()
        try:
            stl_path = _make_stl(tmp, scale=50.0)
            glb_path = str(tmp / "output.glb")
            converter = STLConverter()
            assert converter.convert(stl_path, glb_path) is True
            validate_glb_quality(glb_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
