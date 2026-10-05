"""Per-scene AR variants: a GLB holding only a saved scene's visible layers.

Mobile AR (Android Scene Viewer, iOS Quick Look) has no layer controls, so a
saved scene that hides or fades layers gets its own GLB (and USDZ) built by the
worker. A viewer layer is one named material (see ``converters.layers``), so
hiding a layer means dropping the mesh primitives that use its materials and
fading one means setting ``alphaMode`` BLEND with the base colour alpha scaled.

Quick Look's handling of BLEND materials is uncertain (the Blender USD export
carries opacity, but RealityKit may render it opaque or with sorting artefacts),
so the USDZ is built with ``round_faded=True``: a layer faded to 50% or more
stays fully visible, below that it is dropped. Android Scene Viewer renders
BLEND correctly and keeps the exact opacity.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

from pygltflib import GLTF2

from .glb_optimize import _find_cli, decompress_glb, glb_has_draco, optimize_glb

logger = logging.getLogger(__name__)

# Faded layers at or above this opacity stay visible when rounding for USDZ.
USDZ_FADE_ROUND_AT = 0.5


def round_faded_layers(hidden: set, faded: dict, threshold: float = USDZ_FADE_ROUND_AT) -> tuple[set, dict]:
    """(hidden, faded) with every faded layer resolved to visible or hidden."""
    hidden = set(hidden)
    for name, opacity in faded.items():
        if opacity < threshold:
            hidden.add(name)
    return hidden, {}


def _drop_hidden_primitives(gltf: GLTF2, hidden_materials: set) -> int:
    """Remove primitives using a hidden material; returns how many primitives remain.

    Meshes left without primitives are removed (glTF requires at least one) and
    the nodes that used them lose their mesh reference; mesh indices are
    renumbered. Nodes themselves stay, so the scene graph remains valid.
    """
    materials = gltf.materials or []
    hidden_indices = {i for i, m in enumerate(materials) if m.name in hidden_materials}
    remap: dict[int, int] = {}
    kept_meshes = []
    remaining = 0
    for index, mesh in enumerate(gltf.meshes or []):
        mesh.primitives = [p for p in (mesh.primitives or []) if p.material not in hidden_indices]
        if mesh.primitives:
            remap[index] = len(kept_meshes)
            kept_meshes.append(mesh)
            remaining += len(mesh.primitives)
    gltf.meshes = kept_meshes
    for node in gltf.nodes or []:
        if node.mesh is not None:
            node.mesh = remap.get(node.mesh)
            if node.mesh is None:
                node.weights = None
    return remaining


def _fade_materials(gltf: GLTF2, faded: dict) -> None:
    for material in gltf.materials or []:
        opacity = faded.get(material.name)
        if opacity is None:
            continue
        pbr = material.pbrMetallicRoughness
        if pbr is None:
            from pygltflib import PbrMetallicRoughness

            pbr = material.pbrMetallicRoughness = PbrMetallicRoughness()
        factor = list(pbr.baseColorFactor or [1.0, 1.0, 1.0, 1.0])
        factor += [1.0] * (4 - len(factor))
        factor[3] = round(float(factor[3]) * max(0.0, min(1.0, float(opacity))), 4)
        pbr.baseColorFactor = factor
        material.alphaMode = "BLEND"


def _prune(path: str) -> None:
    """Best-effort ``gltf-transform prune``: drops the accessors and buffers the
    removed primitives left behind (it never merges materials). Keeps the file as is on failure."""
    tmp = path + ".pruned.glb"
    try:
        result = subprocess.run(
            _find_cli() + ["prune", path, tmp],
            capture_output=True, text=True, timeout=120, cwd=os.path.dirname(path) or None,
        )
        if result.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) >= 20:
            shutil.move(tmp, path)
    except (OSError, subprocess.SubprocessError):
        logger.info("gltf-transform prune unavailable; scene variant keeps unused buffers")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def build_scene_variant(
    source_glb: str,
    out_glb: str,
    hidden_materials: set,
    faded: dict,
    *,
    round_faded: bool = False,
    compress: bool = True,
) -> bool:
    """Write ``out_glb``: ``source_glb`` without ``hidden_materials`` and with
    ``faded`` (material name -> opacity 0..1) blended.

    ``round_faded`` resolves faded layers to visible/hidden instead (see the module
    docstring; used for USDZ). ``compress=False`` keeps the output Draco-free,
    which Blender (USDZ export) needs. Returns False, never raises, when the
    variant could not be built (missing/undecodable source, nothing left to show).
    """
    hidden_materials, faded = set(hidden_materials or ()), dict(faded or {})
    if round_faded:
        hidden_materials, faded = round_faded_layers(hidden_materials, faded)
    try:
        with tempfile.TemporaryDirectory(prefix="academicar-scene-variant-") as tmp:
            work = os.path.join(tmp, "variant.glb")
            if glb_has_draco(source_glb):
                if not decompress_glb(source_glb, work):
                    return False
            else:
                shutil.copyfile(source_glb, work)
            gltf = GLTF2.load(work)
            if _drop_hidden_primitives(gltf, hidden_materials) == 0:
                logger.warning("Scene variant would be empty; not built")
                return False
            _fade_materials(gltf, faded)
            gltf.save(work)
            _prune(work)
            if compress:
                optimize_glb(work, keep_layers=True)  # best effort: stays plain if gltf-transform is missing
            os.makedirs(os.path.dirname(out_glb) or ".", exist_ok=True)
            partial = out_glb + ".part"
            shutil.copyfile(work, partial)
            os.replace(partial, out_glb)
        return True
    except Exception:
        logger.exception("Scene variant build failed for %s", source_glb)
        try:
            if os.path.exists(out_glb + ".part"):
                os.remove(out_glb + ".part")
        except OSError:
            pass
        return False
