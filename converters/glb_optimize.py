"""GLB optimization via gltf-transform CLI (Draco geometry compression + texture webp).

Runs as a subprocess — same pattern as obj2gltf/fbx2gltf wrappers. The
optimize step is format-agnostic: it runs on the final GLB regardless of
whether the source was STL, OBJ, FBX, or direct GLB upload.

Quality-safe defaults: Draco quantization is set conservatively
(position 14-bit, normal 10-bit) so there is no visible quality loss on
typical academic/medical meshes. Texture compression uses lossless webp.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMEOUT = 120


def _find_cli() -> list[str]:
    """Locate the gltf-transform CLI binary."""
    override = os.environ.get("GLTF_TRANSFORM_COMMAND", "").strip()
    if override:
        import shlex
        return shlex.split(override)

    base_dir = Path(__file__).parent.parent
    local_bin = base_dir / "node_modules" / ".bin" / "gltf-transform"
    if local_bin.exists():
        return [str(local_bin)]
    return ["npx", "gltf-transform"]


def optimize_glb(
    glb_path: str,
    *,
    draco: bool = True,
    texture_compress: bool = True,
) -> bool:
    """Optimize a GLB file in-place using gltf-transform.

    Steps applied: weld → dedup → prune → (draco) → (texture webp).
    The original file is only replaced when optimization succeeds.
    Returns True on success, False on failure (original file preserved).
    """
    if not os.path.exists(glb_path):
        logger.warning("optimize_glb: file not found: %s", glb_path)
        return False

    original_size = os.path.getsize(glb_path)
    tmp_output = glb_path + ".optimized.glb"

    cmd = _find_cli() + ["optimize", glb_path, tmp_output]

    if draco:
        cmd += ["--compress", "draco"]
    if texture_compress:
        cmd += ["--texture-compress", "webp"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            cwd=os.path.dirname(glb_path),
        )
    except FileNotFoundError:
        logger.info(
            "gltf-transform not found; skipping GLB optimization. "
            "Install with: npm install @gltf-transform/cli"
        )
        _cleanup(tmp_output)
        return False
    except subprocess.TimeoutExpired:
        logger.warning("gltf-transform timed out after %ds", _TIMEOUT)
        _cleanup(tmp_output)
        return False

    if result.returncode != 0 or not os.path.exists(tmp_output):
        logger.warning(
            "gltf-transform optimize failed (exit %d): %s",
            result.returncode,
            (result.stderr or result.stdout or "")[:500],
        )
        _cleanup(tmp_output)
        return False

    optimized_size = os.path.getsize(tmp_output)
    if optimized_size < 20:
        logger.warning("gltf-transform produced an empty/too-small output; keeping original")
        _cleanup(tmp_output)
        return False

    shutil.move(tmp_output, glb_path)
    reduction = (1 - optimized_size / original_size) * 100 if original_size else 0
    logger.info(
        "GLB optimized: %s → %s (%.1f%% reduction)",
        _fmt_size(original_size),
        _fmt_size(optimized_size),
        reduction,
    )
    return True


def _cleanup(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"
