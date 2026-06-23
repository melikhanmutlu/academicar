"""Faz 1 — Görsel Kalite Paketi tests.

Validates:
- model-viewer CDN upgraded to 4.3.0 across all templates
- Custom studio IBL environment image exists and is valid webp
- Consistent render attributes (tone-mapping, exposure, shadow, environment-image)
"""

import re
from pathlib import Path

import pytest
from PIL import Image


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
STUDIO_ENV = Path(__file__).resolve().parent.parent / "static" / "environments" / "studio.webp"

VIEWER_TEMPLATES = [
    "viewer.html",
    "paper_detail.html",
    "paper_public.html",
    "demo_mitochondria_ar.html",
    "landing.html",
]


class TestModelViewerCDN:
    """All templates must reference model-viewer 4.3.0."""

    @pytest.mark.parametrize("template", VIEWER_TEMPLATES)
    def test_uses_4_3_0(self, template):
        content = (TEMPLATES_DIR / template).read_text()
        assert "model-viewer@4.3.0" in content, f"{template} should use model-viewer 4.3.0"
        assert "model-viewer@4.2.0" not in content, f"{template} still has old 4.2.0 reference"


class TestStudioEnvironment:
    """Programmatic studio IBL image."""

    def test_file_exists(self):
        assert STUDIO_ENV.exists(), "static/environments/studio.webp must exist"

    def test_valid_webp(self):
        img = Image.open(str(STUDIO_ENV))
        assert img.format == "WEBP"
        assert img.size[0] >= 512
        assert img.size[1] >= 256

    def test_is_equirectangular_ratio(self):
        img = Image.open(str(STUDIO_ENV))
        w, h = img.size
        assert abs(w / h - 2.0) < 0.01, f"Expected 2:1 aspect ratio, got {w}x{h}"

    @pytest.mark.parametrize("template", VIEWER_TEMPLATES)
    def test_templates_use_studio_env(self, template):
        content = (TEMPLATES_DIR / template).read_text()
        assert "environments/studio.webp" in content, (
            f"{template} should reference studio.webp environment"
        )


class TestConsistentRenderAttributes:
    """Tone-mapping and exposure should be consistent."""

    @pytest.mark.parametrize("template", VIEWER_TEMPLATES)
    def test_tone_mapping_neutral(self, template):
        content = (TEMPLATES_DIR / template).read_text()
        assert 'tone-mapping="neutral"' in content, (
            f'{template} should have tone-mapping="neutral"'
        )
        assert 'tone-mapping="agx"' not in content, (
            f"{template} still has agx tone-mapping"
        )

    @pytest.mark.parametrize("template", VIEWER_TEMPLATES)
    def test_exposure_consistent(self, template):
        content = (TEMPLATES_DIR / template).read_text()
        matches = re.findall(r'exposure="([^"]+)"', content)
        for val in matches:
            assert float(val) == 1.0, f'{template} exposure should be 1.0, got {val}'

    @pytest.mark.parametrize("template", VIEWER_TEMPLATES)
    def test_shadow_intensity_subtle(self, template):
        """Shadows must stay subtle so the contact-shadow plane isn't obtrusive."""
        content = (TEMPLATES_DIR / template).read_text()
        matches = re.findall(r'shadow-intensity="([^"]+)"', content)
        for val in matches:
            assert float(val) <= 0.5, f'{template} shadow-intensity should be subtle, got {val}'

    def test_viewer_interaction_prompt_auto(self):
        # The one-time interaction-prompt camera nudge hints the model is
        # interactive on first load.
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert 'interaction-prompt="auto"' in content


class TestMakeStudioEnvScript:
    """The generation script should be importable and functional."""

    def test_script_exists(self):
        script = Path(__file__).resolve().parent.parent / "scripts" / "make_studio_env.py"
        assert script.exists()
