"""
STL format to GLB format conversion operations using trimesh.
"""

import os
import logging
import re
import struct
import trimesh
import numpy as np
from .base_converter import BaseConverter


# Inline utility functions (replacing deleted utils/)
def ensure_directory(path):
    """Ensure directory exists, create if needed."""
    import os

    os.makedirs(path, exist_ok=True)


def safe_delete_file(path):
    """Safely delete a file if it exists."""
    import os

    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def is_valid_extension(filename, extensions):
    """Check if filename has valid extension."""
    return any(filename.lower().endswith(ext) for ext in extensions)


logger = logging.getLogger(__name__)


def _numpy2_allclose(a, b, atol=1e-8):
    return float(np.ptp(np.asanyarray(a) - np.asanyarray(b))) < atol


trimesh.util.allclose = _numpy2_allclose


def _srgb_to_linear(c: float) -> float:
    """Convert a single sRGB channel (0..1) to linear color space.

    glTF 2.0 spec requires baseColorFactor to be in LINEAR color space.
    Color picker hex values are sRGB. Without this conversion, strict
    PBR renderers (iOS Quick Look) display the color ~2.2x too bright,
    which looks washed-out compared to the user's intent. Lenient
    renderers (three.js with tone-mapping=agx) tone-map the over-bright
    value back into a similar-looking color, which is why the desktop
    preview hid this bug while iOS exposed it.
    """
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def convert_glb_to_usdz(glb_path: str, usdz_path: str) -> bool:
    """Convert a GLB to USDZ so iOS Quick Look can render it without Apple's
    lossy auto-conversion (which strips normals and produces a smooth blob).

    Tries the optional ``aspose-3d`` package. If it isn't installed (it is
    proprietary and large), returns False — the caller should treat USDZ as
    optional and continue with GLB only. Users can also upload a hand-made
    USDZ via the model upload form as a fallback.
    """
    try:
        import aspose.threed as a3d  # type: ignore
    except ImportError:
        logger.info(
            "aspose-3d not installed; skipping GLB->USDZ. "
            "Install with `pip install aspose-3d` to enable automatic iOS USDZ generation."
        )
        return False

    try:
        scene = a3d.Scene.from_file(glb_path)
        scene.save(usdz_path)
        ok = os.path.exists(usdz_path) and os.path.getsize(usdz_path) > 0
        if ok:
            logger.info("Converted GLB -> USDZ: %s", usdz_path)
        return ok
    except Exception as e:
        logger.warning("GLB -> USDZ conversion failed (%s); USDZ will be unavailable", e)
        return False


def enrich_glb_for_ar(
    glb_path: str,
    base_color_rgba: tuple,
    roughness: float = 0.45,
    metallic: float = 0.05,
    double_sided: bool = True,
) -> bool:
    """Post-process a GLB so that:

    1. Every primitive has a PBR material with linear baseColorFactor +
       roughness/metallic factors and doubleSided=True. This fixes iOS
       Quick Look and Android Scene Viewer rendering the model as stark
       white when only COLOR_0 vertex attributes are present.
    2. Every primitive has TEXCOORD_0 UV coordinates, generated via
       triplanar box projection from vertex positions and the dominant
       axis of each vertex's normal. STL meshes ship without UVs; some
       AR pipelines (notably the THREE.USDZExporter that model-viewer
       uses on iOS) handle un-UV'd geometry oddly, contributing to the
       smoothed/blob look.

    Implemented with pygltflib so the GLB binary is rebuilt by a
    well-tested library instead of by hand-rolled JSON+struct code.
    Returns True on success, False if pygltflib isn't installed (in
    which case the caller falls back to the legacy raw injector).
    """
    try:
        import pygltflib
        from pygltflib import Accessor, BufferView, Material, PbrMetallicRoughness
    except ImportError:
        logger.info(
            "pygltflib not installed; falling back to raw struct injector "
            "(no triplanar UVs). Install with `pip install pygltflib`."
        )
        return False

    try:
        gltf = pygltflib.GLTF2.load(glb_path)
    except Exception as e:
        logger.warning("pygltflib failed to load GLB %s (%s); falling back", glb_path, e)
        return False

    # 1) Material: append a fresh AcademicAR_Default material and link
    #    every primitive to it. Existing materials are kept (could be
    #    referenced by other primitives in mixed scenes) but every
    #    primitive ends up pointing at our explicit one for AR consistency.
    material = Material(
        name="AcademicAR_Default",
        pbrMetallicRoughness=PbrMetallicRoughness(
            baseColorFactor=[float(c) for c in base_color_rgba],
            metallicFactor=float(metallic),
            roughnessFactor=float(roughness),
        ),
        doubleSided=bool(double_sided),
    )
    if gltf.materials is None:
        gltf.materials = []
    material_index = len(gltf.materials)
    gltf.materials.append(material)
    for mesh_def in gltf.meshes or []:
        for prim in mesh_def.primitives or []:
            prim.material = material_index

    # 2) Triplanar UVs for primitives missing TEXCOORD_0
    blob = gltf.binary_blob() or b""
    extra = bytearray()

    for mesh_def in gltf.meshes or []:
        for prim in mesh_def.primitives or []:
            if getattr(prim.attributes, "TEXCOORD_0", None) is not None:
                continue
            pos_idx = getattr(prim.attributes, "POSITION", None)
            if pos_idx is None:
                continue

            pos_acc = gltf.accessors[pos_idx]
            pos_bv = gltf.bufferViews[pos_acc.bufferView]
            pos_off = (pos_bv.byteOffset or 0) + (pos_acc.byteOffset or 0)
            pos_stride = pos_bv.byteStride or 12

            positions = []
            for v in range(pos_acc.count):
                off = pos_off + v * pos_stride
                positions.append(struct.unpack_from("<3f", blob, off))

            normals = None
            norm_idx = getattr(prim.attributes, "NORMAL", None)
            if norm_idx is not None:
                n_acc = gltf.accessors[norm_idx]
                n_bv = gltf.bufferViews[n_acc.bufferView]
                n_off = (n_bv.byteOffset or 0) + (n_acc.byteOffset or 0)
                n_stride = n_bv.byteStride or 12
                normals = []
                for v in range(n_acc.count):
                    off = n_off + v * n_stride
                    normals.append(struct.unpack_from("<3f", blob, off))

            xs = [p[0] for p in positions]
            ys = [p[1] for p in positions]
            zs = [p[2] for p in positions]
            min_v = (min(xs), min(ys), min(zs))
            max_v = (max(xs), max(ys), max(zs))
            rng = tuple((max_v[i] - min_v[i]) or 1.0 for i in range(3))

            uv_bytes = bytearray()
            for i, (x, y, z) in enumerate(positions):
                ux = (x - min_v[0]) / rng[0]
                uy = (y - min_v[1]) / rng[1]
                uz = (z - min_v[2]) / rng[2]
                if normals and i < len(normals):
                    anx, any_, anz = abs(normals[i][0]), abs(normals[i][1]), abs(normals[i][2])
                else:
                    anx, any_, anz = 0.0, 0.0, 1.0
                if anx >= any_ and anx >= anz:
                    u, v = uz, uy
                elif any_ >= anx and any_ >= anz:
                    u, v = ux, uz
                else:
                    u, v = ux, uy
                uv_bytes += struct.pack("<2f", u, v)

            new_bv_offset = len(blob) + len(extra)
            new_bv = BufferView(
                buffer=0,
                byteOffset=new_bv_offset,
                byteLength=len(uv_bytes),
            )
            bv_index = len(gltf.bufferViews)
            gltf.bufferViews.append(new_bv)
            new_acc = Accessor(
                bufferView=bv_index,
                byteOffset=0,
                componentType=5126,  # FLOAT
                count=pos_acc.count,
                type="VEC2",
                max=[1.0, 1.0],
                min=[0.0, 0.0],
            )
            acc_index = len(gltf.accessors)
            gltf.accessors.append(new_acc)
            prim.attributes.TEXCOORD_0 = acc_index
            extra += uv_bytes

    if extra:
        new_blob = blob + bytes(extra)
        gltf.set_binary_blob(new_blob)
        if gltf.buffers:
            gltf.buffers[0].byteLength = len(new_blob)

    try:
        gltf.save(glb_path)
    except Exception as e:
        logger.warning("pygltflib failed to save GLB %s (%s)", glb_path, e)
        return False

    logger.info(
        "Enriched GLB %s: PBR material + %d primitives received triplanar UVs",
        os.path.basename(glb_path),
        sum(1 for m in (gltf.meshes or []) for p in (m.primitives or [])),
    )
    return True


def inject_pbr_material(
    glb_path: str,
    base_color_rgba: tuple,
    roughness: float = 0.45,
    metallic: float = 0.05,
    double_sided: bool = True,
) -> None:
    """Rewrite a GLB file in place, attaching a PBR material with the given
    baseColorFactor to every primitive.

    trimesh exports vertex colors via the COLOR_0 vertex attribute, but iOS
    Quick Look (USDZ conversion) and several Android Scene Viewer paths
    ignore COLOR_0 when rendering AR — the model appears stark white. By
    injecting a proper PBR material with baseColorFactor we get consistent
    color rendering across desktop three.js, iOS Quick Look, and Android
    Scene Viewer. doubleSided=true also avoids invisible back faces on
    non-watertight anatomical meshes.

    Operates on the JSON chunk only; binary buffers are left untouched.
    """
    import json
    import struct

    with open(glb_path, "rb") as f:
        data = f.read()

    if len(data) < 20:
        raise ValueError("GLB file too small to be valid")

    magic, version, _total = struct.unpack("<4sII", data[:12])
    if magic != b"glTF" or version != 2:
        raise ValueError(f"Unexpected GLB header: magic={magic!r}, version={version}")

    json_length, json_type = struct.unpack("<II", data[12:20])
    if json_type != 0x4E4F534A:  # 'JSON' little-endian
        raise ValueError(f"First chunk is not JSON (type=0x{json_type:08x})")

    json_bytes = data[20 : 20 + json_length]
    gltf = json.loads(json_bytes.decode("utf-8").rstrip("\x00"))

    materials = gltf.setdefault("materials", [])
    new_index = len(materials)
    materials.append(
        {
            "name": "AcademicAR_Default",
            "pbrMetallicRoughness": {
                "baseColorFactor": [float(c) for c in base_color_rgba],
                "metallicFactor": float(metallic),
                "roughnessFactor": float(roughness),
            },
            "doubleSided": bool(double_sided),
        }
    )

    for mesh_def in gltf.get("meshes", []):
        for primitive in mesh_def.get("primitives", []):
            primitive["material"] = new_index

    new_json = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    pad = (4 - len(new_json) % 4) % 4
    new_json += b" " * pad

    rest = data[20 + json_length :]
    new_total = 12 + 8 + len(new_json) + len(rest)

    with open(glb_path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, new_total))
        f.write(struct.pack("<II", len(new_json), 0x4E4F534A))
        f.write(new_json)
        f.write(rest)


def load_stl_mesh_without_normals(file_path: str) -> trimesh.Trimesh:
    """Load ASCII or binary STL without passing face normals to old trimesh."""
    with open(file_path, "rb") as file:
        header = file.read(512)

    if header.lower().lstrip().startswith(b"solid") and (
        b"facet" in header.lower() or b"endsolid" in header.lower()
    ):
        text = open(file_path, "r", encoding="utf-8", errors="ignore").read()
        vertex_lines = re.findall(
            r"vertex\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)",
            text,
        )
        vertices = np.array(vertex_lines, dtype=np.float64)
        if len(vertices) < 3 or len(vertices) % 3 != 0:
            raise ValueError("ASCII STL does not contain complete triangle vertices.")
        faces = np.arange(len(vertices), dtype=np.int64).reshape((-1, 3))
        return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)

    with open(file_path, "rb") as file:
        file.seek(80)
        triangle_count = struct.unpack("<I", file.read(4))[0]
        payload = file.read(triangle_count * 50)

    if len(payload) < triangle_count * 50:
        raise ValueError("Binary STL payload is incomplete.")

    vertices = np.zeros((triangle_count * 3, 3), dtype=np.float64)
    for index in range(triangle_count):
        offset = index * 50 + 12
        triangle = struct.unpack_from("<9f", payload, offset)
        vertices[index * 3 : index * 3 + 3] = np.array(triangle, dtype=np.float64).reshape((3, 3))
    faces = np.arange(len(vertices), dtype=np.int64).reshape((-1, 3))
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


class STLConverter(BaseConverter):
    """Converter for STL files to GLB format using trimesh."""

    def __init__(self):
        super().__init__()
        self.supported_extensions = {".stl"}
        self.logger = logging.getLogger(__name__)

    def validate(self, file_path: str) -> bool:
        """
        Validate STL file
        Args:
            file_path: Path of the file to be checked
        Returns:
            bool: Is the file valid
        """
        if not super().validate(file_path):
            return False

        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext not in self.supported_extensions:
            self.handle_error(f"Unsupported file format: {file_ext}")
            return False

        try:
            # Try to load the STL file to validate it
            mesh = trimesh.load(file_path)
            if not isinstance(mesh, (trimesh.Trimesh, trimesh.Scene)):
                self.handle_error("Invalid STL file format")
                return False
        except Exception as e:
            self.handle_error(f"Error validating STL file: {str(e)}")
            return False

        return True

    def convert(self, input_path: str, output_path: str, color: str = None) -> bool:
        """
        Convert STL file to GLB format using trimesh
        Args:
            input_path: Path of the STL file to be converted
            output_path: Path of the output GLB file
            color: Optional color to apply to the mesh
        Returns:
            bool: Was the conversion successful
        """
        try:
            self.update_status("CONVERTING")
            self.log_operation("Starting STL to GLB conversion")
            self.log_operation(f"Input: {input_path}")
            self.log_operation(f"Output: {output_path}")

            # Create output directory
            ensure_directory(os.path.dirname(output_path))

            # Load the STL file
            self.log_operation("Loading STL file...")
            mesh = load_stl_mesh_without_normals(input_path)

            if not isinstance(mesh, (trimesh.Trimesh, trimesh.Scene)):
                self.handle_error(f"Invalid mesh type: {type(mesh)}")
                return False

            # Ensure we have a scene to process and flatten all node transforms
            if isinstance(mesh, trimesh.Trimesh):
                scene = trimesh.Scene([mesh])
            else:
                scene = mesh

            flattened_meshes = []
            for node_name in scene.graph.nodes_geometry:
                transform, geom_name = scene.graph[node_name]
                geometry = scene.geometry.get(geom_name)
                if geometry is None:
                    continue
                geom_copy = geometry.copy()
                geom_copy.apply_transform(transform)
                flattened_meshes.append(geom_copy)

            if not flattened_meshes:
                self.handle_error("No geometry found in STL scene")
                return False

            if len(flattened_meshes) == 1:
                mesh = flattened_meshes[0]
            else:
                mesh = trimesh.util.concatenate(flattened_meshes)

            self.log_operation(
                f"Flattened scene: {len(flattened_meshes)} geometries merged into single mesh"
            )

            # Apply basis correction once (Z-up -> Y-up) directly to vertices
            basis_correction = trimesh.transformations.rotation_matrix(
                angle=np.radians(-90), direction=[1, 0, 0]
            )
            mesh.apply_transform(basis_correction)
            self.log_operation("Applied basis correction: -90° around X (Z-up to Y-up)")

            # STL files are unitless. Detect the source unit heuristically from
            # the bounding-box extents BEFORE scaling. GLB standard requires meters.
            #
            # Typical academic STLs (medical/dental from Slicer/Mimics, CAD parts):
            #   - millimeters: extents in tens to thousands  (a 7 cm bone -> 70)
            #   - centimeters: extents in single to hundreds (a 7 cm bone -> 7)
            #   - meters:      extents already < ~10         (a 7 cm bone -> 0.07)
            raw_extents = np.ptp(mesh.bounds, axis=0)
            max_extent_raw = float(raw_extents.max())
            self.log_operation(
                f"Raw STL extents (unitless): x={raw_extents[0]:.3f}, "
                f"y={raw_extents[1]:.3f}, z={raw_extents[2]:.3f}"
            )

            if max_extent_raw > 100.0:
                unit_scale, unit_label = 0.001, "mm"
            elif max_extent_raw > 10.0:
                unit_scale, unit_label = 0.01, "cm"
            else:
                unit_scale, unit_label = 1.0, "m"
            mesh.apply_scale(unit_scale)
            self.log_operation(
                f"Detected source unit '{unit_label}' (max extent {max_extent_raw:.2f}); "
                f"applied scale {unit_scale} -> meters"
            )

            # Center the mesh on the origin so model-viewer / AR placement uses a
            # predictable pivot (otherwise an off-origin model can be placed far
            # from the AR floor reticle and appear missing).
            try:
                center = mesh.bounding_box.centroid
                mesh.apply_translation(-center)
                self.log_operation(
                    f"Centered mesh on origin (translated by {-center})"
                )
            except Exception as e:
                self.log_operation(
                    f"Warning: could not center mesh on origin: {e}", "WARNING"
                )

            # Force trimesh to compute vertex_normals so the GLB exporter
            # writes a NORMAL accessor. We deliberately keep the unwelded
            # one-vertex-per-triangle-corner topology produced by
            # load_stl_mesh_without_normals — each vertex then belongs to a
            # single face, so the lazily computed vertex_normals equal the
            # face normals (= flat per-face shading, the look anatomical
            # STLs need). Without this access, the exporter writes the GLB
            # without normals and AR engines must derive them at runtime
            # (often inconsistently between desktop three.js and iOS USDZ).
            try:
                _ = mesh.vertex_normals
                self.log_operation(
                    f"Forced vertex_normals computation for export "
                    f"({len(mesh.vertices)} verts, {len(mesh.faces)} faces)"
                )
            except Exception as e:
                self.log_operation(f"Warning: vertex_normals access failed: {e}", "WARNING")

            # Get model dimensions (now in meters, consistent with GLB standard)
            extents = np.ptp(mesh.bounds, axis=0)

            dimensions = {"x": extents[0], "y": extents[1], "z": extents[2]}

            self.log_operation(f"Model dimensions (meters): {dimensions}")

            # Calculate scale factor only if max_dimension was explicitly set by user
            # Default max_dimension is 0.5 but we only scale if user checked the checkbox
            if self.max_dimension > 0:
                scale_factor = self.calculate_scale_factor(dimensions)
                if scale_factor != 1.0:
                    self.log_operation(f"Applying scale factor: {scale_factor}")
                    if isinstance(mesh, trimesh.Scene):
                        for geom in mesh.geometry.values():
                            if isinstance(geom, trimesh.Trimesh):
                                geom.apply_scale(scale_factor)
                    else:
                        mesh.apply_scale(scale_factor)
                else:
                    self.log_operation(
                        "No scaling needed - model already at target size"
                    )
            else:
                self.log_operation("No scaling applied - max_dimension not set by user")

            # Determine target color (hex -> RGBA float in 0..1) for material.
            # We DO NOT apply vertex colors anymore: iOS Quick Look (USDZ) and many
            # Android Scene Viewer paths ignore COLOR_0 vertex attributes when they
            # render AR, so a vertex-colored mesh appears stark white in AR even
            # though the desktop three.js viewer shows it correctly. Instead we
            # inject a PBR material with baseColorFactor into the exported GLB
            # below; this is read by both desktop and AR engines uniformly.
            # Default light gray, expressed in LINEAR color space (sRGB #cccccc
            # -> linear ~0.604). All baseColorFactor values are linear per the
            # glTF 2.0 spec; renderers gamma-correct on output.
            target_color = (0.6038, 0.6038, 0.6038, 1.0)
            if color:
                try:
                    hex_color = color.lstrip("#")
                    if len(hex_color) != 6:
                        raise ValueError(f"Invalid hex color: {color}")
                    srgb_r = int(hex_color[0:2], 16) / 255.0
                    srgb_g = int(hex_color[2:4], 16) / 255.0
                    srgb_b = int(hex_color[4:6], 16) / 255.0
                    target_color = (
                        _srgb_to_linear(srgb_r),
                        _srgb_to_linear(srgb_g),
                        _srgb_to_linear(srgb_b),
                        1.0,
                    )
                    self.log_operation(
                        f"Target PBR color parsed from '{color}' "
                        f"(sRGB {srgb_r:.3f},{srgb_g:.3f},{srgb_b:.3f} -> "
                        f"linear {target_color[0]:.3f},{target_color[1]:.3f},{target_color[2]:.3f})"
                    )
                except Exception as e:
                    self.log_operation(
                        f"Warning: could not parse color '{color}': {e}; "
                        f"falling back to default light gray",
                        "WARNING",
                    )
            else:
                self.log_operation(
                    "No color specified; using default light gray (linear 0.604) for PBR material"
                )

            # Note: Basis correction (Z-up to Y-up) is NOT applied here
            # It will be handled in glb_modifier during normalization
            # This keeps the model in its original orientation on upload

            # Convert to scene if it's a single mesh
            if isinstance(mesh, trimesh.Trimesh):
                self.log_operation("Converting mesh to scene")
                scene = trimesh.Scene([mesh])
            else:
                scene = mesh

            # Export as GLB
            self.log_operation("Exporting to GLB format")
            scene.export(output_path)

            if not os.path.exists(output_path):
                self.handle_error("Output file was not created")
                return False

            # Post-process: enrich the GLB with a PBR material AND triplanar
            # UVs so AR engines (iOS Quick Look, Android Scene Viewer) get a
            # complete primitive — explicit baseColorFactor (linear), doubleSided,
            # roughness/metallic, and TEXCOORD_0 for shaders that need it.
            # Falls back to the legacy raw-struct injector (material only, no
            # UVs) if pygltflib isn't installed.
            try:
                enriched = enrich_glb_for_ar(output_path, target_color)
                if enriched:
                    self.log_operation(
                        f"Enriched GLB via pygltflib: PBR material "
                        f"baseColorFactor={target_color} (roughness=0.45, "
                        f"metallic=0.05, doubleSided=true) + triplanar UVs"
                    )
                else:
                    inject_pbr_material(output_path, target_color)
                    self.log_operation(
                        f"Injected PBR material via raw struct fallback "
                        f"(no UVs): baseColorFactor={target_color}"
                    )
            except Exception as e:
                self.log_operation(
                    f"Warning: GLB enrichment failed: {e}; "
                    f"AR may render flat/white.",
                    "WARNING",
                )

            file_size = os.path.getsize(output_path)
            self.log_operation(
                f"STL file converted successfully. Output size: {file_size} bytes"
            )
            return True

        except Exception as e:
            self.handle_error(f"Error during conversion: {str(e)}")
            import traceback

            self.log_operation(f"Traceback: {traceback.format_exc()}")
            return False

    def handle_error(self, error_message: str) -> None:
        """
        Handle and log error messages
        Args:
            error_message: Error message to be logged
        """
        self.errors.append(error_message)
        self.update_status("ERROR")
        self.log_operation(f"Error during conversion: {error_message}")
