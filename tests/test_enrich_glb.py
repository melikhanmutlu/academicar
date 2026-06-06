"""Regression test: enrich_glb_for_ar (the recolor step) must not crash on
Draco-compressed GLBs, whose accessors have no bufferView. The material/color
change must still be applied. Reproduces the production error:
"TypeError: list indices must be integers or slices, not NoneType"."""


def _make_draco_like_glb(path):
    """A minimal GLB whose primitive is Draco-compressed: the POSITION accessor
    has no bufferView and the geometry lives in a KHR_draco_mesh_compression
    extension (exactly the shape that broke the recolor)."""
    import pygltflib
    from pygltflib import (
        GLTF2,
        Accessor,
        Attributes,
        Buffer,
        BufferView,
        Mesh,
        Node,
        Primitive,
        Scene,
    )

    blob = b"\x00" * 48  # stand-in for the draco-compressed payload
    gltf = GLTF2()
    gltf.scene = 0
    gltf.scenes = [Scene(nodes=[0])]
    gltf.nodes = [Node(mesh=0)]
    gltf.buffers = [Buffer(byteLength=len(blob))]
    gltf.bufferViews = [BufferView(buffer=0, byteOffset=0, byteLength=len(blob))]
    gltf.accessors = [
        Accessor(bufferView=None, byteOffset=0, componentType=5126, count=3, type="VEC3", min=[0, 0, 0], max=[1, 1, 1])
    ]
    prim = Primitive(attributes=Attributes(POSITION=0), mode=4)
    prim.extensions = {"KHR_draco_mesh_compression": {"bufferView": 0, "attributes": {"POSITION": 0}}}
    gltf.meshes = [Mesh(primitives=[prim])]
    gltf.extensionsUsed = ["KHR_draco_mesh_compression"]
    gltf.extensionsRequired = ["KHR_draco_mesh_compression"]
    gltf.set_binary_blob(blob)
    gltf.save(path)


def test_enrich_recolors_draco_glb_without_crashing(tmp_path):
    import pygltflib

    from converters.stl_converter import enrich_glb_for_ar

    path = str(tmp_path / "draco.glb")
    _make_draco_like_glb(path)

    # Must not raise (previously: TypeError on gltf.bufferViews[None]).
    ok = enrich_glb_for_ar(path, (1.0, 0.0, 0.0, 1.0))
    assert ok is True

    g = pygltflib.GLTF2.load(path)
    assert g.materials, "recolor material was not added"
    bcf = g.materials[-1].pbrMetallicRoughness.baseColorFactor
    assert [round(x, 3) for x in bcf[:3]] == [1.0, 0.0, 0.0]

    prim = g.meshes[0].primitives[0]
    assert prim.material == len(g.materials) - 1  # primitive now uses the new material
    assert "KHR_draco_mesh_compression" in (prim.extensions or {})  # geometry preserved
    assert getattr(prim.attributes, "TEXCOORD_0", None) is None  # UV gen skipped for draco
