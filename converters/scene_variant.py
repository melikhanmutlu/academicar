"""Per-scene AR variants: a GLB holding only a saved scene's visible layers
(recoloured and cut by its section plane when the scene has them).

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


def _srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _recolor_materials(gltf: GLTF2, colors: dict) -> None:
    """Set the base colour (``#rrggbb``, sRGB) of the named materials, keeping their alpha."""
    from pygltflib import PbrMetallicRoughness

    for material in gltf.materials or []:
        hex_color = colors.get(material.name)
        if not hex_color:
            continue
        pbr = material.pbrMetallicRoughness or PbrMetallicRoughness()
        material.pbrMetallicRoughness = pbr
        factor = list(pbr.baseColorFactor or [1.0, 1.0, 1.0, 1.0])
        factor += [1.0] * (4 - len(factor))
        rgb = [_srgb_to_linear(int(hex_color[k:k + 2], 16) / 255) for k in (1, 3, 5)]
        pbr.baseColorFactor = [round(c, 6) for c in rgb] + [factor[3]]


def section_cut(path: str, section: dict) -> bool:
    """Cut the (Draco-free) GLB at ``path`` in place with the viewer's section plane.

    The plane is the viewer's: glTF scene space, axis ``x|y|z`` at the centre of
    the union of every mesh's transformed bounding-box corners plus ``offset``
    metres; the lower side is kept, or the upper one with ``flip``. Like the
    viewer the cut is not capped: every material becomes double-sided so the
    inside shows. Meshes are baked into scene space (node names and materials
    are kept). Returns False when nothing is left or the file cannot be cut.
    """
    import numpy as np
    import trimesh

    axis = "xyz".index(section["axis"])
    scene = trimesh.load(path, force="scene", process=False)
    nodes = [(node, *scene.graph[node]) for node in scene.graph.nodes_geometry]
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for _, transform, geom_name in nodes:
        corners = trimesh.transform_points(trimesh.bounds.corners(scene.geometry[geom_name].bounds), transform)
        lo, hi = np.minimum(lo, corners.min(axis=0)), np.maximum(hi, corners.max(axis=0))
    origin = np.zeros(3)
    origin[axis] = (lo[axis] + hi[axis]) / 2 + float(section.get("offset") or 0.0)
    normal = np.zeros(3)
    normal[axis] = 1.0 if section.get("flip") else -1.0
    out = trimesh.Scene()
    for node, transform, geom_name in nodes:
        mesh = scene.geometry[geom_name].copy()
        if not isinstance(mesh, trimesh.Trimesh):
            continue
        mesh.apply_transform(transform)
        material = getattr(mesh.visual, "material", None)
        uv = getattr(mesh.visual, "uv", None)
        # slice_faces_plane, not Trimesh.slice_plane: that one needs shapely, used only for capping.
        vertices, faces, uv = trimesh.intersections.slice_faces_plane(
            mesh.vertices, mesh.faces, plane_normal=normal, plane_origin=origin, uv=uv
        )
        if len(faces) == 0:
            continue
        cut = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        if material is not None:
            material = material.copy()
            if hasattr(material, "doubleSided"):
                material.doubleSided = True
            cut.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
        out.add_geometry(cut, node_name=node, geom_name=node)
    if not out.geometry:
        return False
    with open(path, "wb") as handle:
        handle.write(out.export(file_type="glb"))
    return True


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
    colors: dict | None = None,
    section: dict | None = None,
    round_faded: bool = False,
    compress: bool = True,
) -> bool:
    """Write ``out_glb``: ``source_glb`` without ``hidden_materials``, with
    ``faded`` (material name -> opacity 0..1) blended, ``colors`` (material
    name -> ``#rrggbb``) applied and, given a ``section`` (the viewer's
    {axis, offset, flip}), cut by that plane (see ``section_cut``).

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
            _recolor_materials(gltf, colors or {})
            gltf.save(work)
            if section and not section_cut(work, section):
                logger.warning("Scene variant would be empty after the section cut; not built")
                return False
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
