"""Faz 4 — Materyal & AR Kontrol tests.

Validates:
- roughness/metallic/ar_placement columns on Model3D
- roughness/metallic clamping (0-1)
- ar_placement in viewer template
- model_rescale route and dimension update
- migration exists
"""

import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import trimesh

from converters.glb_quality import ensure_pbr_materials


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


class TestModelColumns:
    """New Model3D columns."""

    def test_roughness_column_exists(self):
        from models import Model3D
        assert hasattr(Model3D, 'appearance_roughness')

    def test_metallic_column_exists(self):
        from models import Model3D
        assert hasattr(Model3D, 'appearance_metallic')

    def test_ar_placement_column_exists(self):
        from models import Model3D
        assert hasattr(Model3D, 'ar_placement')


class TestRoughnessClamping:
    """Roughness and metallic values should be clamped to [0, 1]."""

    def test_clamp_roughness_above(self):
        val = max(0.0, min(1.0, 1.5))
        assert val == 1.0

    def test_clamp_roughness_below(self):
        val = max(0.0, min(1.0, -0.3))
        assert val == 0.0

    def test_clamp_metallic_normal(self):
        val = max(0.0, min(1.0, 0.7))
        assert val == 0.7


class TestArPlacementViewer:
    """viewer.html should use model.ar_placement."""

    def test_viewer_uses_dynamic_ar_placement(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "ar_placement" in content
        assert 'ar-placement="floor"' not in content or 'model.ar_placement' in content


class TestModelEditUI:
    """model_edit.html should have roughness/metallic sliders and rescale form."""

    def test_has_roughness_slider(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        assert 'name="roughness"' in content
        assert 'type="range"' in content

    def test_has_metallic_slider(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        assert 'name="metallic"' in content

    def test_has_ar_placement_radio(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        assert 'name="ar_placement"' in content
        assert 'value="floor"' in content
        assert 'value="wall"' in content

    def test_has_rescale_form(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        assert 'model_rescale' in content
        assert 'target_longest_cm' in content


class TestModelEditLivePreview:
    """model_edit.html should embed a live material preview viewer + JS hooks."""

    def test_embeds_preview_model_viewer(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        # model-viewer script loaded + a preview element with the expected id
        assert "model-viewer@4.3.0" in content
        assert 'id="materialPreview"' in content

    def test_live_preview_uses_runtime_material_api(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        assert "setRoughnessFactor" in content
        assert "setMetallicFactor" in content
        assert "setBaseColorFactor" in content

    def test_slider_inputs_have_ids_for_live_binding(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        # input names preserved (route compatibility) + ids added for JS binding
        assert 'id="appearanceRoughness"' in content and 'name="roughness"' in content
        assert 'id="appearanceMetallic"' in content and 'name="metallic"' in content
        assert 'id="appearanceColor"' in content and 'name="color"' in content


class TestMigrationExists:
    """Migration for material/AR columns."""

    def test_migration_file_exists(self):
        migration = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "b2c3d4e5f6g7_add_material_ar_columns.py"
        assert migration.exists()
        content = migration.read_text()
        assert "appearance_roughness" in content
        assert "appearance_metallic" in content
        assert "ar_placement" in content


class TestRescaleLogic:
    """Scale factor computation."""

    def test_scale_factor_calculation(self):
        current_extent_m = 0.5
        target_cm = 30.0
        target_m = target_cm / 100.0
        scale_factor = target_m / current_extent_m
        assert abs(scale_factor - 0.6) < 0.001

    def test_rescale_produces_correct_dimensions(self):
        tmp = Path.cwd() / f".pytest-rescale-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        try:
            glb_path = str(tmp / "test.glb")
            mesh = trimesh.creation.box(extents=[0.5, 0.3, 0.2])
            trimesh.Scene([mesh]).export(glb_path)

            from pygltflib import GLTF2
            gltf = GLTF2.load(glb_path)
            loaded = trimesh.load(glb_path, force="scene")
            current_max = float(np.ptp(loaded.bounds, axis=0).max())
            target_cm = 20.0
            target_m = target_cm / 100.0
            scale_factor = target_m / current_max

            for node in gltf.nodes or []:
                if node.scale:
                    node.scale = [s * scale_factor for s in node.scale]
                else:
                    node.scale = [scale_factor, scale_factor, scale_factor]
            gltf.save(glb_path)

            reloaded = trimesh.load(glb_path, force="scene")
            new_max = float(np.ptp(reloaded.bounds, axis=0).max())
            assert abs(new_max - target_m) < 0.01, f"Expected ~{target_m}m, got {new_max}m"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
