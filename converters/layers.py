"""Toggleable "layers" for multi-part GLBs (STEP assemblies, multi-mesh GLBs).

model-viewer can only reach a model's materials (by name), so a viewer layer is
a node or colour group whose materials are owned by that layer alone.
``normalize_layers`` rewrites the GLB in place so that holds, and
``read_layers`` reports the same layer list back without touching the file.

Repeated parts (20 bolts of an assembly) are one layer: mesh nodes whose names
differ only by a trailing instance suffix share a layer and its material.
"""

from __future__ import annotations

import copy
import logging
import re

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


# Trailing instance markers CAD exports add to repeated parts: "Bolt (2)",
# "Bolt:1", "Bolt #2", "Bolt.001", "Bolt_1", "Bolt-1". A space before a bare
# number ("Label 1", "Bolt 2") is NOT one: those are usually distinct parts.
_INSTANCE_SUFFIX = re.compile(r"^(?P<base>.*?[^\W\d_].*?)(?:\s*\(\d+\)|\s*[:#]\s*\d+|[._-]\d+)$")


def _base_name(name: str) -> str:
    """``name`` without one trailing instance suffix (the base must keep a letter)."""
    match = _INSTANCE_SUFFIX.match(name.strip())
    return match.group("base").strip() if match else name.strip()


def _top_level_groups(gltf: GLTF2, nodes: list[int]) -> list[tuple[str, list[int], bool]]:
    """Mesh nodes grouped by the top-level child of the scene root (sub-assemblies).

    Single-child wrappers (STEP root, a lone assembly node) are looked through.
    """
    all_nodes = gltf.nodes or []
    if gltf.scenes:
        scene_index = gltf.scene if gltf.scene is not None and gltf.scene < len(gltf.scenes) else 0
        tops = list(gltf.scenes[scene_index].nodes or [])
    else:
        children = {c for n in all_nodes for c in (n.children or [])}
        tops = [i for i in range(len(all_nodes)) if i not in children]
    while len(tops) == 1 and 0 <= tops[0] < len(all_nodes) and all_nodes[tops[0]].mesh is None and all_nodes[tops[0]].children:
        tops = list(all_nodes[tops[0]].children)
    mesh_nodes = set(nodes)
    groups: list[tuple[str, list[int], bool]] = []
    for ordinal, top in enumerate(tops, start=1):
        members, stack, seen = [], [top], set()
        while stack:
            index = stack.pop()
            if index in seen or not 0 <= index < len(all_nodes):
                continue
            seen.add(index)
            if index in mesh_nodes:
                members.append(index)
            stack.extend(all_nodes[index].children or [])
        if members:
            members.sort(key=nodes.index)
            groups.append((_node_label(gltf, top, ordinal), members, False))
    return groups


def _layer_groups(gltf: GLTF2, nodes: list[int], max_layers: int) -> list[tuple[str, list[int], bool]] | None:
    """Layer groups (unique name, node indices, repeats-of-one-part flag) for
    >= 2 mesh nodes, or None.

    Nodes sharing a base name are one layer; if that is still more than
    ``max_layers`` the sub-assemblies are the layers. When everything collapses
    into a single group (one part repeated, or "Rib-1"/"Rib-2" that are really
    different parts) each node stays its own layer, as long as there are few.
    """
    by_base: dict[object, list[int]] = {}
    for ordinal, index in enumerate(nodes, start=1):
        raw = (gltf.nodes[index].name or "").strip()
        key = _base_name(raw) if raw else ("", ordinal)
        by_base.setdefault(key, []).append(index)

    def named(groups: list[tuple[str, list[int], bool]]) -> list[tuple[str, list[int], bool]]:
        used: set[str] = set()
        return [(_unique(name, used), members, repeats) for name, members, repeats in groups]

    groups = []
    for key, members in by_base.items():
        if len(members) > 1:
            groups.append((key, members, True))
        else:
            groups.append((_node_label(gltf, members[0], nodes.index(members[0]) + 1), members, False))
    if 2 <= len(groups) <= max_layers:
        return named(groups)
    if len(groups) < 2 and len(nodes) <= max_layers:
        return named([(_node_label(gltf, index, ordinal), [index], False) for ordinal, index in enumerate(nodes, start=1)])
    groups = _top_level_groups(gltf, nodes)
    return named(groups) if 2 <= len(groups) <= max_layers else None


def _group_material_indices(gltf: GLTF2, members: list[int]) -> list[int]:
    found: list[int] = []
    for index in members:
        found.extend(i for i in _node_material_indices(gltf, index) if i not in found)
    return found


def _with_count(layer: dict, members: list[int], repeats: bool) -> dict:
    if repeats and len(members) > 1:
        layer["count"] = len(members)
    return layer


def read_layers(glb_path: str) -> list[dict]:
    """Report the layers of a GLB (same shape as ``normalize_layers``) without changing it."""
    try:
        gltf = _load_glb(glb_path)
    except GLBQualityError:
        return []
    nodes = _mesh_nodes(gltf)
    if len(nodes) >= 2:
        groups = _layer_groups(gltf, nodes, MAX_LAYERS)
        if groups is None:
            return []
        return [
            _with_count(_layer_dict(gltf, name, _group_material_indices(gltf, members)), members, repeats)
            for name, members, repeats in groups
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

    Layers are groups of mesh-carrying nodes (repeats of one part form one
    layer) when there are at least two nodes, otherwise distinct materials.
    Returns ``[]`` and leaves the file untouched when there are fewer than two
    layers or more than ``max_layers``.
    """
    try:
        gltf = _load_glb(glb_path)
    except GLBQualityError:
        return []

    nodes = _mesh_nodes(gltf)
    if len(nodes) >= 2:
        groups = _layer_groups(gltf, nodes, max_layers)
        if groups is None:
            return []
        layers = _normalize_node_layers(gltf, groups)
    else:
        materials = _scene_material_indices(gltf)
        if not 2 <= len(materials) <= max_layers:
            return []
        layers = _normalize_material_layers(gltf, materials)

    gltf.save(glb_path)
    return layers


def _normalize_node_layers(gltf: GLTF2, groups: list[tuple[str, list[int], bool]]) -> list[dict]:
    if gltf.materials is None:
        gltf.materials = []
    names = [name for name, _, _ in groups]

    # Reserve every layer name first so extra-material names ("<layer> · 2")
    # can never collide with another layer's name.
    used: set[str] = set(names)

    # A mesh shared between layers is cloned so each layer can own materials;
    # repeats inside one layer keep sharing their mesh (and so its geometry).
    mesh_owner: dict[int, int] = {}
    for group_index, (_, members, _) in enumerate(groups):
        for index in members:
            node = gltf.nodes[index]
            if mesh_owner.setdefault(node.mesh, group_index) != group_index:
                gltf.meshes.append(copy.deepcopy(gltf.meshes[node.mesh]))
                node.mesh = len(gltf.meshes) - 1
                mesh_owner[node.mesh] = group_index

    def key_of(primitive) -> int | None:
        return primitive.material if primitive.material is not None and 0 <= primitive.material < len(gltf.materials) else None

    def distinct_keys(index: int) -> list[int | None]:
        keys: list[int | None] = []
        for primitive in gltf.meshes[gltf.nodes[index].mesh].primitives or []:
            if key_of(primitive) not in keys:
                keys.append(key_of(primitive))
        return keys

    touched = {i for _, members, _ in groups for index in members for i in distinct_keys(index) if i is not None}
    claimed: set[int] = set()
    layers = []
    for name, members, repeats in groups:
        own: dict[int | None, int] = {}  # original material index -> this layer's material
        order: list[int] = []
        for position_in_group, index in enumerate(members):
            for j, key in enumerate(distinct_keys(index)):
                if key in own:
                    continue
                if order and position_in_group > 0:
                    # A repeat with a different material layout: line up by position.
                    own[key] = order[min(j, len(order) - 1)]
                    continue
                if key is None:
                    gltf.materials.append(default_material(name))
                    own[key] = len(gltf.materials) - 1
                elif key in claimed:
                    gltf.materials.append(copy.deepcopy(gltf.materials[key]))
                    own[key] = len(gltf.materials) - 1
                else:
                    claimed.add(key)
                    own[key] = key
                order.append(own[key])
            for primitive in gltf.meshes[gltf.nodes[index].mesh].primitives or []:
                primitive.material = own[key_of(primitive)]
        for position, material_index in enumerate(order):
            if position == 0:
                gltf.materials[material_index].name = name
            else:
                gltf.materials[material_index].name = _unique(f"{name} · {position + 1}", used)
        layers.append(_with_count(_layer_dict(gltf, name, order), members, repeats))

    _drop_orphaned_materials(gltf, touched)
    return layers


def _drop_orphaned_materials(gltf: GLTF2, candidates: set[int]) -> None:
    """Remove ``candidates`` materials no primitive uses any more (leftovers of
    repeats folded into one layer), renumbering the references."""
    used = {p.material for mesh in gltf.meshes for p in mesh.primitives or [] if p.material is not None}
    drop = {i for i in candidates if i not in used}
    if not drop:
        return
    remap, kept = {}, []
    for old, material in enumerate(gltf.materials):
        if old not in drop:
            remap[old] = len(kept)
            kept.append(material)
    gltf.materials = kept
    for mesh in gltf.meshes:
        for primitive in mesh.primitives or []:
            if primitive.material is not None:
                primitive.material = remap[primitive.material]


def _normalize_material_layers(gltf: GLTF2, material_indices: list[int]) -> list[dict]:
    used: set[str] = set()
    layers = []
    for ordinal, index in enumerate(material_indices, start=1):
        name = _unique((gltf.materials[index].name or "").strip() or f"Part {ordinal}", used)
        gltf.materials[index].name = name
        layers.append(_layer_dict(gltf, name, [index]))
    return layers
