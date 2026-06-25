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


class TestClampOversizedGlb:
    """Absurdly large models (unit errors) are scaled down; reasonable ones aren't."""

    def _glb(self, tmp, longest):
        path = str(tmp / "m.glb")
        trimesh.Scene([trimesh.creation.box(extents=[longest, 1, 1])]).export(path)
        return path

    def test_absurd_size_clamped(self):
        from converters.glb_scale import clamp_oversized_glb, measure_glb_max_extent_m
        tmp = _tmp_dir()
        try:
            path = self._glb(tmp, 100.0)  # 100 m, way over the 50 m threshold
            assert clamp_oversized_glb(path) is True
            assert 1.5 < measure_glb_max_extent_m(path) < 2.5
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_reasonable_large_not_clamped(self):
        from converters.glb_scale import clamp_oversized_glb, measure_glb_max_extent_m
        tmp = _tmp_dir()
        try:
            path = self._glb(tmp, 3.0)  # 3 m, under threshold -> untouched
            assert clamp_oversized_glb(path) is False
            assert abs(measure_glb_max_extent_m(path) - 3.0) < 0.15
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestMaskCutoutTextures:
    """Textured BLEND materials (FBX foliage) become MASK so leaves render crisp."""

    def test_blend_textured_becomes_mask(self):
        from pygltflib import GLTF2, Material, PbrMetallicRoughness, TextureInfo
        from converters.glb_quality import _set_cutout_on_blend_textured

        g = GLTF2()
        g.materials = [
            Material(alphaMode="BLEND",
                     pbrMetallicRoughness=PbrMetallicRoughness(baseColorTexture=TextureInfo(index=0))),
            Material(alphaMode="OPAQUE",
                     pbrMetallicRoughness=PbrMetallicRoughness(baseColorTexture=TextureInfo(index=0))),
            Material(alphaMode="BLEND", pbrMetallicRoughness=PbrMetallicRoughness()),  # no texture
        ]
        assert _set_cutout_on_blend_textured(g) is True
        assert g.materials[0].alphaMode == "MASK"
        assert g.materials[0].alphaCutoff == 0.5
        assert g.materials[1].alphaMode == "OPAQUE"   # opaque untouched
        assert g.materials[2].alphaMode == "BLEND"    # untextured blend untouched


class TestBakeBaseColorFactor:
    """Non-white baseColorFactor tint is baked into the baseColorTexture so the
    colour survives Blender's USD export (which drops factor multiplies)."""

    def test_color_math_roundtrips(self):
        import numpy as np
        from converters.glb_quality import _linear_to_srgb, _srgb_to_linear

        xs = np.linspace(0.0, 1.0, 21)
        back = _linear_to_srgb(_srgb_to_linear(xs))
        assert np.allclose(back, xs, atol=1e-6)

    def _glb_with_textured_material(self, tmp, factor, base_gray=0.5):
        """Minimal GLB: one box, one material with an embedded solid-gray RGBA
        baseColorTexture and the given baseColorFactor."""
        import io
        import numpy as np
        from PIL import Image as PILImage
        from pygltflib import (
            GLTF2, Buffer, BufferView, Image, Material, Mesh, Primitive,
            PbrMetallicRoughness, Texture, TextureInfo, Accessor, Scene, Node,
        )

        # Build a tiny GLB via trimesh, then attach our textured material.
        glb_path = str(tmp / "tex.glb")
        trimesh.Scene([trimesh.creation.box(extents=[1, 1, 1])]).export(glb_path)
        ensure_pbr_materials(glb_path)
        from pygltflib import GLTF2 as _G
        g = _G.load(glb_path)

        # Solid neutral-gray RGBA PNG (opaque) as the base color texture.
        px = int(round(base_gray * 255))
        im = PILImage.fromarray(
            np.full((4, 4, 4), [px, px, px, 255], dtype=np.uint8), "RGBA"
        )
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        png = buf.getvalue()

        blob = g.binary_blob() or b""
        pad = b"\x00" * ((4 - len(blob) % 4) % 4)
        offset = len(blob) + len(pad)
        g.set_binary_blob(blob + pad + png)
        g.buffers[0].byteLength = len(g.binary_blob())
        bv_index = len(g.bufferViews)
        g.bufferViews.append(BufferView(buffer=0, byteOffset=offset, byteLength=len(png)))
        img_index = len(g.images or [])
        if g.images is None:
            g.images = []
        g.images.append(Image(bufferView=bv_index, mimeType="image/png"))
        tex_index = len(g.textures or [])
        if g.textures is None:
            g.textures = []
        g.textures.append(Texture(source=img_index))

        g.materials[0].pbrMetallicRoughness = PbrMetallicRoughness(
            baseColorFactor=list(factor),
            baseColorTexture=TextureInfo(index=tex_index),
        )
        for mesh in g.meshes or []:
            for prim in mesh.primitives or []:
                prim.material = 0
        g.save(glb_path)
        return glb_path

    def test_green_factor_baked_into_texture(self):
        import io
        import numpy as np
        from PIL import Image as PILImage
        from pygltflib import GLTF2
        from converters.glb_quality import (
            _image_bytes_for, _srgb_to_linear, bake_base_color_factor,
        )

        tmp = _tmp_dir()
        try:
            # Green tint (linear) on a 50%-gray texture.
            factor = [0.0, 0.6, 0.0, 1.0]
            glb = self._glb_with_textured_material(tmp, factor, base_gray=0.5)
            assert bake_base_color_factor(glb) is True

            g = GLTF2.load(glb)
            mat = g.materials[0]
            # Factor RGB reset to white, alpha preserved.
            assert mat.pbrMetallicRoughness.baseColorFactor[:3] == [1.0, 1.0, 1.0]
            assert mat.pbrMetallicRoughness.baseColorFactor[3] == 1.0

            # The material now points at a NEW texture whose pixels carry the tint.
            new_tex = g.textures[mat.pbrMetallicRoughness.baseColorTexture.index]
            payload = _image_bytes_for(g, g.images[new_tex.source])
            with PILImage.open(io.BytesIO(payload)) as im:
                arr = np.asarray(im.convert("RGBA"), dtype=np.float64) / 255.0
            lin = _srgb_to_linear(arr[..., :3])
            # Expected: srgb_to_linear(0.5) * factor.
            expected = _srgb_to_linear(0.5) * np.array([0.0, 0.6, 0.0])
            assert np.allclose(lin.reshape(-1, 3).mean(axis=0), expected, atol=0.02)
            # Red and blue channels are now ~0 (green-only tint); green non-zero.
            mean = arr.reshape(-1, 4).mean(axis=0)
            assert mean[0] < 0.05 and mean[2] < 0.05 and mean[1] > 0.1
            assert mean[3] > 0.99  # alpha preserved

            validate_glb_quality(glb)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_white_factor_is_noop(self):
        from converters.glb_quality import bake_base_color_factor

        tmp = _tmp_dir()
        try:
            glb = self._glb_with_textured_material(tmp, [1.0, 1.0, 1.0, 1.0])
            assert bake_base_color_factor(glb) is False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_untextured_factor_untouched(self):
        from pygltflib import GLTF2, Material, PbrMetallicRoughness
        from converters.glb_quality import bake_base_color_factor

        tmp = _tmp_dir()
        try:
            glb = _make_test_glb(tmp)  # box with default material, no texture
            g = GLTF2.load(glb)
            g.materials[0].pbrMetallicRoughness.baseColorFactor = [0.0, 0.6, 0.0, 1.0]
            g.save(glb)
            assert bake_base_color_factor(glb) is False
            g2 = GLTF2.load(glb)
            assert g2.materials[0].pbrMetallicRoughness.baseColorFactor == [0.0, 0.6, 0.0, 1.0]
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
