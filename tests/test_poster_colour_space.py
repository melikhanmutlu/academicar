"""The poster rasteriser paints display (sRGB) colours; glTF baseColorFactor is linear."""
import numpy as np
import trimesh

from converters.poster import _vertex_colors_for


def _box_with_factor(factor):
    mesh = trimesh.creation.box()
    mesh.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(baseColorFactor=factor)
    )
    return mesh


def test_linear_base_colour_factor_is_encoded_to_srgb():
    # #22aa55 picked in the viewer is stored as its linear factor.
    linear = [0.015996, 0.401978, 0.090842, 1.0]
    colours = _vertex_colors_for(_box_with_factor(linear))
    assert tuple(int(c) for c in colours[0]) == (34, 170, 85)


def test_mid_grey_is_not_darkened():
    colours = _vertex_colors_for(_box_with_factor([0.2158605, 0.2158605, 0.2158605, 1.0]))
    assert np.all(np.abs(colours[0].astype(int) - 128) <= 1)
