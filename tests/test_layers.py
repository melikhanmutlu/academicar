import copy

import pytest
import trimesh
from pygltflib import GLTF2, TextureInfo

from converters.layers import normalize_layers, read_layers


def _mat(name, rgba=(0.5, 0.5, 0.5, 1.0)):
    return trimesh.visual.material.PBRMaterial(name=name, baseColorFactor=list(rgba))


def _make_glb(path, parts):
    """parts: (node_name, geom_name, material, offset) tuples; material may be shared."""
    scene = trimesh.Scene()
    for node_name, geom_name, material, offset in parts:
        mesh = trimesh.creation.box(extents=(1, 1, 1))
        mesh.apply_translation((offset, 0, 0))
        if material is not None:
            mesh.visual = trimesh.visual.TextureVisuals(material=material)
        kwargs = {"geom_name": geom_name}
        if node_name is not None:
            kwargs["node_name"] = node_name
        scene.add_geometry(mesh, **kwargs)
    scene.export(str(path))
    return path


def _renamed_copy(material, name):
    clone = copy.deepcopy(material)
    clone.name = name
    return clone


def _material_names(path):
    return [m.name for m in GLTF2.load(str(path)).materials]


def test_named_parts_with_same_colour_get_distinct_materials(tmp_path):
    parts = [(n, f"g{i}", _mat("same"), i * 2) for i, n in enumerate(["Housing", "Shaft", "Cap"])]
    glb = _make_glb(tmp_path / "a.glb", parts)

    layers = normalize_layers(str(glb))

    assert [l["name"] for l in layers] == ["Housing", "Shaft", "Cap"]
    assert [l["materials"] for l in layers] == [["Housing"], ["Shaft"], ["Cap"]]
    assert len(set(_material_names(glb))) == len(_material_names(glb)) >= 3


def test_shared_material_is_cloned_not_renamed_in_place(tmp_path):
    shared = _mat("shared", (0.2, 0.4, 0.6, 1.0))
    glb = _make_glb(tmp_path / "a.glb", [("A", "ga", shared, 0), ("B", "gb", shared, 2)])

    layers = normalize_layers(str(glb))

    gltf = GLTF2.load(str(glb))
    by_name = {m.name: m for m in gltf.materials}
    assert set(by_name) >= {"A", "B"}
    assert by_name["A"].pbrMetallicRoughness.baseColorFactor == by_name["B"].pbrMetallicRoughness.baseColorFactor
    assert layers[0]["color"] == layers[1]["color"]
    used = {p.material for m in gltf.meshes for p in m.primitives}
    assert len(used) == 2


def test_mesh_instanced_by_two_nodes_is_cloned(tmp_path):
    glb = _make_glb(tmp_path / "a.glb", [("Bolt", "g0", _mat("m"), 0), ("Bolt 2", "g1", None, 2)])
    gltf = GLTF2.load(str(glb))
    first, second = [n for n in gltf.nodes if n.mesh is not None]
    second.mesh = first.mesh  # instance the same mesh from both nodes
    gltf.save(str(glb))

    layers = normalize_layers(str(glb))

    after = GLTF2.load(str(glb))
    mesh_ids = [n.mesh for n in after.nodes if n.mesh is not None]
    assert len(set(mesh_ids)) == 2
    assert [l["name"] for l in layers] == ["Bolt", "Bolt 2"]
    assert len(set(_material_names(glb))) == 2
    # geometry is shared, only the primitive dicts are copied
    accessors = {after.meshes[i].primitives[0].attributes.POSITION for i in mesh_ids}
    assert len(accessors) == 1


def test_unnamed_nodes_become_part_n(tmp_path):
    glb = _make_glb(tmp_path / "a.glb", [(None, "g0", _mat("x"), 0), (None, "g1", _mat("y"), 2)])
    gltf = GLTF2.load(str(glb))
    for node in gltf.nodes:
        if node.mesh is not None:
            node.name = ""
    gltf.save(str(glb))

    layers = normalize_layers(str(glb))

    assert [l["name"] for l in layers] == ["Part 1", "Part 2"]


def test_duplicate_names_are_deduplicated(tmp_path):
    parts = [("B1", "g0", _mat("x"), 0), ("B2", "g1", _mat("y"), 2), ("B3", "g2", _mat("z"), 4)]
    glb = _make_glb(tmp_path / "a.glb", parts)
    gltf = GLTF2.load(str(glb))
    for node in gltf.nodes:
        if node.mesh is not None:
            node.name = "Bolt"
    gltf.save(str(glb))

    layers = normalize_layers(str(glb))

    assert [l["name"] for l in layers] == ["Bolt", "Bolt (2)", "Bolt (3)"]
    assert [l["materials"] for l in layers] == [["Bolt"], ["Bolt (2)"], ["Bolt (3)"]]


def test_node_without_material_gets_default_pbr(tmp_path):
    glb = _make_glb(tmp_path / "a.glb", [("A", "ga", None, 0), ("B", "gb", None, 2)])
    gltf = GLTF2.load(str(glb))
    for mesh in gltf.meshes:
        for primitive in mesh.primitives:
            primitive.material = None
    gltf.save(str(glb))

    layers = normalize_layers(str(glb))

    assert [l["materials"] for l in layers] == [["A"], ["B"]]
    pbr = {m.name: m.pbrMetallicRoughness for m in GLTF2.load(str(glb)).materials}["A"]
    assert pbr.baseColorFactor == pytest.approx([0.6038] * 3 + [1.0])
    assert pbr.metallicFactor == pytest.approx(0.05)
    assert pbr.roughnessFactor == pytest.approx(0.35)


def test_node_with_several_materials_keeps_them_all_uniquely_named(tmp_path):
    glb = _make_glb(tmp_path / "a.glb", [("Pair", "gp", _mat("p1"), 0), ("Other", "go", _mat("o"), 2)])
    gltf = GLTF2.load(str(glb))
    mesh = gltf.meshes[gltf.nodes[[n.name for n in gltf.nodes].index("Pair")].mesh]
    extra = copy.deepcopy(mesh.primitives[0])
    gltf.materials.append(_renamed_copy(gltf.materials[mesh.primitives[0].material], "p2"))
    extra.material = len(gltf.materials) - 1
    mesh.primitives.append(extra)
    gltf.save(str(glb))

    layers = normalize_layers(str(glb))

    assert layers[0]["materials"] == ["Pair", "Pair · 2"]
    assert layers[1]["materials"] == ["Other"]
    names = _material_names(glb)
    assert len(names) == len(set(names))


def test_single_mesh_returns_empty_and_leaves_file_unchanged(tmp_path):
    glb = _make_glb(tmp_path / "a.glb", [("Only", "g", _mat("m"), 0)])
    before = glb.read_bytes()

    assert normalize_layers(str(glb)) == []
    assert glb.read_bytes() == before


def test_more_than_max_layers_returns_empty_and_leaves_file_unchanged(tmp_path):
    glb = _make_glb(tmp_path / "a.glb", [(f"P{i}", f"g{i}", _mat("m"), i * 2) for i in range(5)])
    before = glb.read_bytes()

    assert normalize_layers(str(glb), max_layers=4) == []
    assert glb.read_bytes() == before
    assert len(normalize_layers(str(glb), max_layers=5)) == 5


def test_material_based_layers_when_single_node(tmp_path):
    # One mesh node whose two primitives use two materials.
    merged = trimesh.Scene()
    a = trimesh.creation.box()
    b = trimesh.creation.box()
    b.apply_translation((2, 0, 0))
    a.visual = trimesh.visual.TextureVisuals(material=_mat("Red", (1.0, 0.0, 0.0, 1.0)))
    b.visual = trimesh.visual.TextureVisuals(material=_mat("Red", (0.0, 0.0, 1.0, 1.0)))
    merged.add_geometry(a, node_name="N1", geom_name="a")
    merged.add_geometry(b, node_name="N2", geom_name="b")
    glb = tmp_path / "a.glb"
    merged.export(str(glb))
    gltf = GLTF2.load(str(glb))
    # fold both primitives into one mesh/node
    first, second = [n for n in gltf.nodes if n.mesh is not None]
    gltf.meshes[first.mesh].primitives.extend(gltf.meshes[second.mesh].primitives)
    gltf.scenes[0].nodes = [gltf.nodes.index(first)]
    gltf.save(str(glb))

    layers = normalize_layers(str(glb))

    assert [l["name"] for l in layers] == ["Red", "Red (2)"]
    assert [l["color"] for l in layers] == ["#ff0000", "#0000ff"]
    assert _material_names(glb) == ["Red", "Red (2)"]


def test_color_is_srgb_hex_of_linear_factor_and_none_when_textured(tmp_path):
    glb = _make_glb(
        tmp_path / "a.glb",
        [("Gray", "g0", _mat("m0", (0.2140, 0.2140, 0.2140, 1.0)), 0), ("Tex", "g1", _mat("m1"), 2)],
    )
    gltf = GLTF2.load(str(glb))
    tex_material = next(m for m in gltf.materials if m.name == "m1")
    tex_material.pbrMetallicRoughness.baseColorTexture = TextureInfo(index=0)
    gltf.save(str(glb))

    layers = normalize_layers(str(glb))

    # linear 0.214 -> sRGB ~0.5 -> 0x80
    assert layers[0]["color"] == "#808080"
    assert layers[1]["color"] is None


def test_read_layers_matches_normalize_and_does_not_modify(tmp_path):
    parts = [(n, f"g{i}", _mat("same", (0.5, 0.1, 0.1, 1.0)), i * 2) for i, n in enumerate(["A", "B", "C"])]
    glb = _make_glb(tmp_path / "a.glb", parts)
    layers = normalize_layers(str(glb))
    after_normalize = glb.read_bytes()

    assert read_layers(str(glb)) == layers
    assert glb.read_bytes() == after_normalize


def test_read_layers_on_unnormalized_single_mesh_is_empty(tmp_path):
    glb = _make_glb(tmp_path / "a.glb", [("Only", "g", _mat("m"), 0)])
    assert read_layers(str(glb)) == []
    assert read_layers(str(tmp_path / "missing.glb")) == []
