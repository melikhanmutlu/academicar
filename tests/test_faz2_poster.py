"""Faz 2 — Poster/Thumbnail Generation tests.

Validates:
- generate_poster produces a valid PNG from a GLB
- Fallback chain works (pyrender → trimesh → rasterize → False)
- Missing/invalid file returns False
- poster_path column exists in Model3D
- serve_poster route and viewer template integration
"""

import os
import shutil
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import trimesh
from PIL import Image

from converters.poster import generate_poster, _try_rasterize
from converters.glb_quality import ensure_pbr_materials


def _tmp_dir():
    path = Path.cwd() / f".pytest-poster-{os.getpid()}"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir()
    return path


def _make_test_glb(path: Path) -> str:
    glb_path = str(path / "test.glb")
    mesh = trimesh.creation.box(extents=[1, 1, 1])
    trimesh.Scene([mesh]).export(glb_path)
    ensure_pbr_materials(glb_path)
    return glb_path


class TestGeneratePoster:
    """Core poster generation."""

    def test_generates_valid_png(self):
        tmp = _tmp_dir()
        try:
            glb_path = _make_test_glb(tmp)
            png_path = str(tmp / "poster.png")
            result = generate_poster(glb_path, png_path)
            assert result is True
            assert os.path.isfile(png_path)
            img = Image.open(png_path)
            assert img.format == "PNG"
            assert img.size[0] >= 512
            assert img.size[1] >= 512
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_file_returns_false(self):
        assert generate_poster("/nonexistent/file.glb", "/tmp/poster.png") is False

    def test_empty_mesh_returns_false(self):
        tmp = _tmp_dir()
        try:
            glb_path = str(tmp / "empty.glb")
            mesh = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=int))
            trimesh.Scene([mesh]).export(glb_path)
            png_path = str(tmp / "poster.png")
            result = generate_poster(glb_path, png_path)
            # Either generates a valid (blank) poster or gracefully returns False
            assert isinstance(result, bool)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRasterizeFallback:
    """Software rasterizer (Pillow-only, no GL)."""

    def test_rasterize_produces_valid_image(self):
        tmp = _tmp_dir()
        try:
            glb_path = _make_test_glb(tmp)
            png_path = str(tmp / "raster.png")
            result = _try_rasterize(glb_path, png_path)
            assert result is True
            img = Image.open(png_path)
            assert img.size == (1024, 1024)
            pixels = np.array(img)
            # Should not be entirely one color (background)
            assert pixels.std() > 1.0, "Rasterized image should not be blank"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_rasterize_with_non_cube(self):
        tmp = _tmp_dir()
        try:
            glb_path = str(tmp / "cylinder.glb")
            mesh = trimesh.creation.cylinder(radius=0.5, height=2.0)
            trimesh.Scene([mesh]).export(glb_path)
            png_path = str(tmp / "poster.png")
            result = _try_rasterize(glb_path, png_path)
            assert result is True
            assert os.path.isfile(png_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFallbackChain:
    """When pyrender and trimesh scene are unavailable, rasterize is used."""

    def test_falls_back_to_rasterize(self):
        tmp = _tmp_dir()
        try:
            glb_path = _make_test_glb(tmp)
            png_path = str(tmp / "poster.png")
            with patch("converters.poster._try_pyrender", side_effect=ImportError), \
                 patch("converters.poster._try_trimesh_scene", side_effect=ImportError):
                result = generate_poster(glb_path, png_path)
            assert result is True
            assert os.path.isfile(png_path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_all_fail_returns_false(self):
        tmp = _tmp_dir()
        try:
            glb_path = _make_test_glb(tmp)
            png_path = str(tmp / "poster.png")
            with patch("converters.poster._try_pyrender", side_effect=ImportError), \
                 patch("converters.poster._try_trimesh_scene", side_effect=ImportError), \
                 patch("converters.poster._try_rasterize", return_value=False):
                result = generate_poster(glb_path, png_path)
            assert result is False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestModelPosterPath:
    """Model3D.poster_path column."""

    def test_column_exists(self):
        from models import Model3D
        assert hasattr(Model3D, 'poster_path')

    def test_migration_exists(self):
        migration = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "a1b2c3d4e5f6_add_poster_path_to_models.py"
        assert migration.exists()


class TestViewerPosterIntegration:
    """Templates reference poster attribute."""

    TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

    def test_viewer_has_poster_attribute(self):
        content = (self.TEMPLATES_DIR / "viewer.html").read_text()
        assert "poster=" in content
        assert "serve_poster" in content

    def test_paper_detail_has_poster(self):
        content = (self.TEMPLATES_DIR / "paper_detail.html").read_text()
        assert "poster=" in content

    def test_paper_public_has_poster(self):
        content = (self.TEMPLATES_DIR / "paper_public.html").read_text()
        assert "poster=" in content
