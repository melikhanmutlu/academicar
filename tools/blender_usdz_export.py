"""Headless Blender script: convert a 3D model (GLB/GLTF/FBX/OBJ/STL) to USDZ.

Invoked as:

    blender --background --python tools/blender_usdz_export.py -- <input> <output.usdz>

Blender's USD exporter writes a correctly-scaled USDZ (proper metersPerUnit),
which is what iOS AR Quick Look needs. This is the same approach used to
generate the `ios-src` companion served by the public viewer, and it works in
both Safari and Chrome on iOS (a real ios-src URL, unlike model-viewer's
client-side blob auto-generation which only launches Quick Look in Safari).

Best-effort: on any failure the process exits non-zero and the caller treats
USDZ as unavailable, falling back to GLB-only.
"""

import os
import sys

import bpy


def export_usdz(input_path, output_path):
    # Start from an empty scene so nothing from factory defaults (camera, light,
    # cube) leaks into the exported USDZ.
    bpy.ops.wm.read_factory_settings(use_empty=True)

    ext = os.path.splitext(input_path)[1].lower()

    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=input_path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=input_path, use_anim=True)
    elif ext == ".obj":
        # Blender 4.x renamed the OBJ importer operator; fall back for 3.x.
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=input_path)
        else:
            bpy.ops.import_scene.obj(filepath=input_path)
    elif ext == ".stl":
        if hasattr(bpy.ops.wm, "stl_import"):
            bpy.ops.wm.stl_import(filepath=input_path)
        else:
            bpy.ops.import_mesh.stl(filepath=input_path)
    else:
        print(f"Unsupported file extension: {ext}")
        sys.exit(1)

    # Diagnostic: what did the importer actually bring in? A mismatch here vs the
    # source GLB (e.g. 0 meshes from a Draco GLB on a Draco-less build) explains a
    # USDZ that differs from the model-viewer render.
    objs = list(bpy.context.scene.objects)
    meshes = [o for o in objs if o.type == "MESH"]
    print(f"USDZ import: {len(objs)} objects, {len(meshes)} meshes from {os.path.basename(input_path)}")
    if not meshes:
        print("No meshes imported — aborting USDZ so a wrong/empty model isn't served.")
        sys.exit(1)

    if not output_path.lower().endswith(".usdz"):
        output_path += ".usdz"

    print(f"Exporting to {output_path}...")

    bpy.ops.object.select_all(action="SELECT")

    # Blender 3.5+ writes .usdz directly from usd_export.
    bpy.ops.wm.usd_export(
        filepath=output_path,
        selected_objects_only=False,
        export_animation=True,
        export_hair=False,
        export_uvmaps=True,
        export_normals=True,
        export_materials=True,
    )

    print("Export successful.")


if __name__ == "__main__":
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []

    if len(argv) < 2:
        print(
            "Usage: blender --background --python blender_usdz_export.py "
            "-- <input_file> <output_file>"
        )
        sys.exit(1)

    try:
        export_usdz(argv[0], argv[1])
    except Exception as exc:  # noqa: BLE001 - surface the reason and fail the run
        print(f"Error during conversion: {exc}")
        sys.exit(1)
