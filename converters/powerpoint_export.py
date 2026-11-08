"""PowerPoint-ready GLB: the model as PowerPoint's Insert > 3D Models can read it.

The stored model.glb is Draco-compressed for the web, which Office's 3D import
cannot be relied on to decode, and a dense scan makes a slide deck slow. This
writes a plain GLB: Draco-free, the owner's saved default layer view applied
(hidden layers left out, faded ones kept faded) and, above
``POWERPOINT_MAX_TRIANGLES``, simplified with gltf-transform (meshoptimizer).
Runs in the worker (``powerpoint_glb`` job), never in a web request.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

from pygltflib import GLTF2

from converters.glb_optimize import _find_cli, decompress_glb, glb_has_draco
from converters.scene_variant import build_scene_variant

logger = logging.getLogger(__name__)

POWERPOINT_MAX_TRIANGLES = int(os.environ.get("POWERPOINT_MAX_TRIANGLES", "400000"))
_TIMEOUT = 300


def count_triangles(glb_path: str) -> int:
    """Triangles drawn by the GLB's triangle-list primitives (mode 4)."""
    gltf = GLTF2.load(glb_path)
    total = 0
    for mesh in gltf.meshes or []:
        for primitive in mesh.primitives or []:
            if primitive.mode not in (None, 4):
                continue
            if primitive.indices is not None:
                total += gltf.accessors[primitive.indices].count // 3
            elif primitive.attributes and primitive.attributes.POSITION is not None:
                total += gltf.accessors[primitive.attributes.POSITION].count // 3
    return total


def _simplify(src: str, dst: str, ratio: float) -> bool:
    cmd = _find_cli() + ["simplify", src, dst, "--ratio", f"{ratio:.4f}", "--error", "0.01"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("gltf-transform simplify unavailable or timed out: %s", exc)
        return False
    if result.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 20:
        logger.warning("gltf-transform simplify failed (exit %d): %s",
                       result.returncode, (result.stderr or result.stdout or "")[:500])
        return False
    return True


def build_powerpoint_glb(
    source_glb: str,
    out_glb: str,
    hidden_materials: set | None = None,
    faded: dict | None = None,
    *,
    max_triangles: int = POWERPOINT_MAX_TRIANGLES,
) -> bool:
    """Write ``out_glb`` (see module docstring). Returns False, never raises,
    when no usable file could be built; ``out_glb`` is only replaced on success."""
    try:
        with tempfile.TemporaryDirectory(prefix="academicar-pptx-glb-") as tmp:
            work = os.path.join(tmp, "plain.glb")
            if hidden_materials or faded:
                if not build_scene_variant(source_glb, work, set(hidden_materials or ()), dict(faded or {}), compress=False):
                    return False
            elif glb_has_draco(source_glb):
                if not decompress_glb(source_glb, work):
                    return False
            else:
                shutil.copyfile(source_glb, work)

            triangles = count_triangles(work)
            if max_triangles and triangles > max_triangles:
                simplified = os.path.join(tmp, "simplified.glb")
                if _simplify(work, simplified, max_triangles / triangles):
                    work = simplified
                    logger.info("PowerPoint GLB simplified: %d -> %d triangles", triangles, count_triangles(work))

            if glb_has_draco(work):
                logger.warning("PowerPoint GLB still Draco-compressed; not served")
                return False
            os.makedirs(os.path.dirname(out_glb) or ".", exist_ok=True)
            part = out_glb + ".part"
            shutil.copyfile(work, part)
            os.replace(part, out_glb)
            return True
    except Exception:
        logger.exception("Could not build the PowerPoint GLB from %s", source_glb)
        return False
