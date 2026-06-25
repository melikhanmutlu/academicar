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

# A model whose longest axis exceeds this (metres) is almost certainly a unit
# error — e.g. an FBX whose units fbx2gltf mis-converted to ~150 m — not an
# intentionally huge model. Such models are scaled down to AR_CLAMP_TARGET_M so
# AR doesn't place a giant object you end up standing inside. The threshold is
# deliberately HIGH so legitimately large models (a 5–30 m statue) pass untouched.
AR_MAX_PLAUSIBLE_EXTENT_M = float(os.environ.get("AR_MAX_PLAUSIBLE_EXTENT_M", "50.0"))
AR_CLAMP_TARGET_M = float(os.environ.get("AR_CLAMP_TARGET_M", "2.0"))


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


def clamp_oversized_glb(glb_path: str) -> bool:
    """Scale down a GLB whose longest axis is implausibly large (a unit error).

    Returns True if a clamp scale was applied; models within
    AR_MAX_PLAUSIBLE_EXTENT_M are left untouched. Must run on the uncompressed
    GLB (before Draco optimization, which trimesh cannot measure).
    """
    extent_m = measure_glb_max_extent_m(glb_path)
    if not extent_m or extent_m <= AR_MAX_PLAUSIBLE_EXTENT_M:
        return False
    factor = AR_CLAMP_TARGET_M / extent_m
    if apply_uniform_scale(glb_path, factor):
        logger.warning(
            "Clamped oversized GLB %s: %.1f m longest axis (likely a unit error) "
            "scaled by %.6g -> ~%.1f m",
            glb_path, extent_m, factor, AR_CLAMP_TARGET_M,
        )
        return True
    return False


def normalize_converted_glb(
    glb_path: str,
    source_unit: str | None,
    *,
    auto_heuristic: bool,
) -> None:
    """Scale a freshly converted GLB by the user-declared source unit.

    ``source_unit`` is mm/cm/m (applied as a metres scale) or anything else
    (e.g. "embedded"/"auto" for FBX/GLB which already carry real units, or a
    legacy job) which is left as-authored. No magnitude guessing and no size
    clamp — large models keep their real size. ``auto_heuristic`` is retained
    for call-site compatibility but no longer changes behaviour.
    """
    unit = (source_unit or "").strip().lower()
    if unit not in _EXPLICIT_UNITS:
        return
    unit_scale = _EXPLICIT_UNITS[unit]
    if abs(unit_scale - 1.0) > 1e-9:
        if apply_uniform_scale(glb_path, unit_scale):
            logger.info("Scaled converted GLB %s by %.6g (unit=%s)", glb_path, unit_scale, unit)
