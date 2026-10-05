"""Toggleable "layers" for multi-part GLBs (STEP assemblies, multi-mesh GLBs).

model-viewer can only reach a model's materials (by name), so a viewer layer is
a node or colour group whose materials are owned by that layer alone.
``normalize_layers`` rewrites the GLB in place so that holds, and
``read_layers`` reports the same layer list back without touching the file.
"""

from __future__ import annotations

import copy
import logging

from pygltflib import GLTF2, Material, PbrMetallicRoughness

from .glb_quality import GLBQualityError, _linear_to_srgb, _load_glb

logger = logging.getLogger(__name__)

# More toggles than this is unusable in the viewer panel and usually means a
# scanned/tessellated mesh split into fragments rather than a real assembly.
MAX_LAYERS = 64


def default_material(name: str) -> Material:
    """Light gray PBR material, matching the STL converter's default look."""
    return Material(
        name=name,
        pbrMetallicRoughness=PbrMetallicRoughness(
            baseColorFactor=[0.6038, 0.6038, 0.6038, 1.0],
            metallicFactor=0.05,
            roughnessFactor=0.35,
        ),
        doubleSided=True,
    )


def _mesh_nodes(gltf: GLTF2) -> list[int]:
    """Indices of nodes that carry a mesh, in scene (depth-first) order."""
    nodes = gltf.nodes or []
    if gltf.scenes:
        scene_index = gltf.scene if gltf.scene is not None and gltf.scene < len(gltf.scenes) else 0
        roots = list(gltf.scenes[scene_index].nodes or [])
    else:
        children = {c for n in nodes for c in (n.children or [])}
        roots = [i for i in range(len(nodes)) if i not in children]
    ordered: list[int] = []
    seen: set[int] = set()
    stack = list(reversed(roots))
    while stack:
        index = stack.pop()
        if index in seen or not 0 <= index < len(nodes):
            continue
        seen.add(index)
        if nodes[index].mesh is not None and 0 <= nodes[index].mesh < len(gltf.meshes or []):
            ordered.append(index)
        stack.extend(reversed(nodes[index].children or []))
    return ordered


def _node_label(gltf: GLTF2, index: int, ordinal: int) -> str:
    return (gltf.nodes[index].name or "").strip() or f"Part {ordinal}"


def _unique(name: str, used: set[str]) -> str:
    """``name`` or ``name (2)``, ``name (3)``... whichever is not in ``used``."""
    candidate, n = name, 2
    while candidate in used:
        candidate = f"{name} ({n})"
        n += 1
    used.add(candidate)
    return candidate


def _color_hex(material: Material | None) -> str | None:
    """sRGB hex of the material's base colour factor; None when textured."""
    if material is None:
        return None
    pbr = material.pbrMetallicRoughness
    if pbr is not None and pbr.baseColorTexture is not None:
        return None
    factor = (pbr.baseColorFactor if pbr is not None and pbr.baseColorFactor else None) or [1.0, 1.0, 1.0, 1.0]
    rgb = _linear_to_srgb([min(max(float(c), 0.0), 1.0) for c in factor[:3]])
    return "#" + "".join(f"{int(round(float(c) * 255)):02x}" for c in rgb)


def _layer_dict(gltf: GLTF2, name: str, material_indices: list[int]) -> dict:
    materials = gltf.materials or []
    return {
        "name": name,
        "materials": [materials[i].name for i in material_indices],
        "color": _color_hex(materials[material_indices[0]]) if material_indices else None,
    }


def _node_material_indices(gltf: GLTF2, node_index: int) -> list[int]:
    """Distinct valid material indices used by a node's mesh, first-use order."""
    count = len(gltf.materials or [])
    found: list[int] = []
    for primitive in gltf.meshes[gltf.nodes[node_index].mesh].primitives or []:
        if primitive.material is not None and 0 <= primitive.material < count and primitive.material not in found:
            found.append(primitive.material)
    return found


def _scene_material_indices(gltf: GLTF2) -> list[int]:
    """Distinct material indices over the scene's meshes (all meshes if no scene)."""
    count = len(gltf.materials or [])
    mesh_ids = [gltf.nodes[i].mesh for i in _mesh_nodes(gltf)] or list(range(len(gltf.meshes or [])))
    found: list[int] = []
    for mesh_id in mesh_ids:
        for primitive in gltf.meshes[mesh_id].primitives or []:
            if primitive.material is not None and 0 <= primitive.material < count and primitive.material not in found:
                found.append(primitive.material)
    return found


def read_layers(glb_path: str) -> list[dict]:
    """Report the layers of a GLB (same shape as ``normalize_layers``) without changing it."""
    try:
        gltf = _load_glb(glb_path)
    except GLBQualityError:
        return []
    nodes = _mesh_nodes(gltf)
    if len(nodes) >= 2:
        return [
            _layer_dict(gltf, _node_label(gltf, index, ordinal), _node_material_indices(gltf, index))
            for ordinal, index in enumerate(nodes, start=1)
        ]
    materials = _scene_material_indices(gltf)
    if len(materials) >= 2:
        return [
            _layer_dict(gltf, (gltf.materials[i].name or "").strip() or f"Part {ordinal}", [i])
            for ordinal, i in enumerate(materials, start=1)
        ]
    return []


def normalize_layers(glb_path: str, *, max_layers: int = MAX_LAYERS) -> list[dict]:
    """Give every part of a multi-part GLB its own uniquely named material(s).

    Layers are mesh-carrying nodes when there are at least two, otherwise
    distinct materials. Returns ``[]`` and leaves the file untouched when there
    are fewer than two layers or more than ``max_layers``.
    """
    try:
        gltf = _load_glb(glb_path)
    except GLBQualityError:
        return []

    nodes = _mesh_nodes(gltf)
    if len(nodes) >= 2:
        if len(nodes) > max_layers:
            return []
        layers = _normalize_node_layers(gltf, nodes)
    else:
        materials = _scene_material_indices(gltf)
        if not 2 <= len(materials) <= max_layers:
            return []
        layers = _normalize_material_layers(gltf, materials)

    gltf.save(glb_path)
    return layers


def _normalize_node_layers(gltf: GLTF2, nodes: list[int]) -> list[dict]:
    if gltf.materials is None:
        gltf.materials = []

    # Reserve every layer name first so extra-material names ("<layer> · 2")
    # can never collide with another layer's name.
    used: set[str] = set()
    names = [_unique(_node_label(gltf, index, ordinal), used) for ordinal, index in enumerate(nodes, start=1)]

    # A mesh instanced by several nodes is cloned so each node can own materials.
    seen_meshes: set[int] = set()
    for index in nodes:
        node = gltf.nodes[index]
        if node.mesh in seen_meshes:
            gltf.meshes.append(copy.deepcopy(gltf.meshes[node.mesh]))
            node.mesh = len(gltf.meshes) - 1
        seen_meshes.add(node.mesh)

    claimed: set[int] = set()
    layers = []
    for index, name in zip(nodes, names):
        own: dict[int | None, int] = {}  # original material index -> this layer's material
        order: list[int] = []
        for primitive in gltf.meshes[gltf.nodes[index].mesh].primitives or []:
            key = primitive.material if primitive.material is not None and 0 <= primitive.material < len(gltf.materials) else None
            if key not in own:
                if key is None:
                    gltf.materials.append(default_material(name))
                elif key in claimed:
                    gltf.materials.append(copy.deepcopy(gltf.materials[key]))
                else:
                    own[key] = key
                    claimed.add(key)
                    order.append(key)
                    primitive.material = key
                    continue
                own[key] = len(gltf.materials) - 1
                order.append(own[key])
            primitive.material = own[key]
        for position, material_index in enumerate(order):
            if position == 0:
                gltf.materials[material_index].name = name
            else:
                gltf.materials[material_index].name = _unique(f"{name} · {position + 1}", used)
        layers.append(_layer_dict(gltf, name, order))
    return layers


def _normalize_material_layers(gltf: GLTF2, material_indices: list[int]) -> list[dict]:
    used: set[str] = set()
    layers = []
    for ordinal, index in enumerate(material_indices, start=1):
        name = _unique((gltf.materials[index].name or "").strip() or f"Part {ordinal}", used)
        gltf.materials[index].name = name
        layers.append(_layer_dict(gltf, name, [index]))
    return layers
