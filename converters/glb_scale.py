"""Uniform real-world scaling for GLB assets.

glTF mandates 1 unit = 1 meter, so a model's real-world AR size is determined
entirely by its geometry/transform magnitudes. Source formats vary:

  * STL / OBJ are unitless — the raw numbers could be mm, cm, or m. STLConverter
    handles its own unit scaling; OBJ is scaled here with the same heuristic.
  * FBX carries a unit (FBX2glTF already bakes it to meters), so its converted
    output is trusted as meters and only clamped if absurdly large.

This module provides the shared primitives:
  * ``measure_glb_max_extent_m`` — largest bounding-box dimension, in meters.
  * ``apply_uniform_scale`` — non-destructively scale a GLB by wrapping its scene
    roots in a new scale node (keeps geometry, materials, textures, Draco buffers
    intact — unlike a trimesh re-export).
  * ``normalize_converted_glb`` — apply a source-unit scale + safety clamp.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# A model larger than this (any axis, in meters) breaks AR placement — it spawns
# huge and flies off-screen. Clamp the longest axis down to this size. Override
# via the AR_MAX_EXTENT_M env var to match the STLConverter clamp.
DEFAULT_MAX_EXTENT_M = float(os.environ.get("AR_MAX_EXTENT_M", "2.0"))

_EXPLICIT_UNITS = {"mm": 0.001, "cm": 0.01, "m": 1.0}


def measure_glb_max_extent_m(glb_path: str) -> float | None:
    """Return the largest bounding-box dimension of a GLB in meters, or None.

    Uses trimesh, which applies the scene-graph transforms (so an existing scale
    node is reflected). Intended for the uncompressed GLB produced mid-pipeline;
    returns None if the geometry cannot be read (e.g. Draco-compressed).
    """
    if not glb_path or not os.path.exists(glb_path):
        return None
    try:
        import numpy as np
        import trimesh

        loaded = trimesh.load(glb_path, force="scene")
        bounds = getattr(loaded, "bounds", None)
        if bounds is None:
            return None
        extent = float(np.ptp(bounds, axis=0).max())
        return extent if extent > 0 else None
    except Exception:
        logger.info("Could not measure GLB extent for %s", glb_path)
        return None


def apply_uniform_scale(glb_path: str, factor: float) -> bool:
    """Scale a GLB uniformly by ``factor`` without touching its geometry.

    Wraps the current scene root nodes in a new parent node carrying the scale.
    This preserves materials, textures, animations, and Draco-compressed buffers
    (a trimesh round-trip would not), and is correct for hierarchical scenes
    (scaling each node individually would compound through parent/child links).
    """
    if factor is None or abs(factor - 1.0) < 1e-9:
        return False
    try:
        from pygltflib import GLTF2, Node

        gltf = GLTF2.load(glb_path)
        scene_index = gltf.scene if gltf.scene is not None else 0
        if not gltf.scenes or scene_index >= len(gltf.scenes):
            return False
        scene = gltf.scenes[scene_index]
        roots = list(scene.nodes or [])
        if not roots:
            return False
        # Insert a scale-carrying node *below* each scene root (adopting the
        # root's mesh + children), rather than wrapping the roots in a new root.
        # A glTF scene root's own transform is honored by spec-compliant renderers
        # (model-viewer, Blender) but ignored by some loaders (trimesh) — pushing
        # the scale one level down is applied consistently everywhere, including
        # the trimesh-based dimension measurement.
        for r in roots:
            root = gltf.nodes[r]
            inner = Node()
            inner.scale = [factor, factor, factor]
            inner.children = list(root.children or [])
            inner.mesh = root.mesh
            gltf.nodes.append(inner)
            inner_index = len(gltf.nodes) - 1
            root.children = [inner_index]
            root.mesh = None
        gltf.save(glb_path)
        return True
    except Exception:
        logger.exception("Failed to apply uniform scale %s to %s", factor, glb_path)
        return False


def normalize_converted_glb(
    glb_path: str,
    source_unit: str | None,
    *,
    auto_heuristic: bool,
) -> None:
    """Scale a freshly converted GLB to a sane real-world size.

    ``source_unit`` is the user's declared source unit (mm/cm/m) or "auto".
    ``auto_heuristic`` selects the "auto" behaviour:
      * True  (OBJ, unitless): guess the unit from the raw extent magnitude.
      * False (FBX, already meters): trust the converter output as-is.
    A safety clamp always caps the longest axis at DEFAULT_MAX_EXTENT_M.
    """
    unit = (source_unit or "auto").strip().lower()
    extent_m = measure_glb_max_extent_m(glb_path)

    # 1) Unit scale.
    if unit in _EXPLICIT_UNITS:
        unit_scale = _EXPLICIT_UNITS[unit]
    elif auto_heuristic and extent_m is not None:
        # Same bands as STLConverter: >1000 µm, >100 mm, >1 cm, else meters.
        if extent_m > 1000.0:
            unit_scale = 0.000001
        elif extent_m > 100.0:
            unit_scale = 0.001
        elif extent_m > 1.0:
            unit_scale = 0.01
        else:
            unit_scale = 1.0
    else:
        unit_scale = 1.0

    scaled_max = (extent_m or 0.0) * unit_scale

    # 2) Safety clamp.
    clamp = 1.0
    if scaled_max > DEFAULT_MAX_EXTENT_M:
        clamp = DEFAULT_MAX_EXTENT_M / scaled_max

    total = unit_scale * clamp
    if abs(total - 1.0) > 1e-9:
        if apply_uniform_scale(glb_path, total):
            logger.info(
                "Scaled converted GLB %s by %.6g (unit=%s, clamp=%.4g) -> ~%.1f cm longest",
                glb_path, total, unit, clamp, (scaled_max * clamp) * 100,
            )
