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

# SEC-6/PERF-3/CONVERT-1: hard caps on mesh complexity. A structurally valid
# but enormous mesh can OOM the conversion worker; these limits reject it early
# with a clear error instead. Overridable via env (0 disables a given check).
MAX_MESH_FACES = int(os.environ.get("MAX_MESH_FACES", 2_000_000))
MAX_MESH_VERTICES = int(os.environ.get("MAX_MESH_VERTICES", 2_000_000))


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
    """Convert a GLB to USDZ for iOS AR Quick Look using headless Blender.

    iOS Quick Look needs a real ``.usdz`` served as ``ios-src`` — it launches
    AR in both Safari *and* Chrome on iOS, whereas model-viewer's client-side
    blob auto-generation only works in Safari (and not at all in in-app
    browsers or cross-origin iframes). Blender's USD exporter also writes the
    correct ``metersPerUnit`` so the model appears at a sane real-world scale
    instead of giant.

    Runs ``blender --background --python tools/blender_usdz_export.py``. If the
    ``blender`` binary isn't on PATH (e.g. local dev without it), returns False
    and the caller treats USDZ as optional, continuing with GLB only.
    """
    import shutil
    import subprocess

    blender_exec = shutil.which("blender") or "blender"
    if shutil.which("blender") is None:
        logger.info(
            "blender not found on PATH; skipping GLB->USDZ. iOS AR will fall "
            "back to GLB-only. Install Blender (it is provided via nixpacks in "
            "production) to enable automatic iOS USDZ generation."
        )
        return False

    # tools/ lives at the repository root, one level up from converters/.
    blender_script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tools",
        "blender_usdz_export.py",
    )

    cmd = [
        blender_exec,
        "--background",
        "--python",
        blender_script,
        "--",
        glb_path,
        usdz_path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=300,  # 5 min — large meshes can be slow to import/export
        )
    except FileNotFoundError:
        logger.info("blender binary not runnable; skipping GLB->USDZ.")
        return False
    except subprocess.TimeoutExpired:
        logger.warning("Blender USDZ conversion timed out after 300s; USDZ unavailable")
        return False

    ok = proc.returncode == 0 and os.path.exists(usdz_path) and os.path.getsize(usdz_path) > 0
    if ok:
        logger.info("Converted GLB -> USDZ via Blender: %s", usdz_path)
    else:
        logger.warning(
            "GLB -> USDZ conversion failed (exit %s); USDZ will be unavailable. "
            "stderr: %s",
            proc.returncode,
            (proc.stderr or proc.stdout or "")[:500],
        )
    return ok


def enrich_glb_for_ar(
    glb_path: str,
    base_color_rgba: tuple,
    roughness: float = 0.35,
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
    well-tested library instead of hand-rolled byte manipulation.
    Returns True on success, False if pygltflib isn't installed.
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

            # Draco-compressed (or otherwise indirect) geometry keeps its vertex
            # data inside a KHR_draco_mesh_compression extension, so the POSITION
            # accessor has no bufferView to read raw floats from. Skip triplanar
            # UV generation for such primitives — the PBR material/color assigned
            # above still applies, which is what a recolor needs. Without this
            # guard, gltf.bufferViews[None] raised
            # "list indices must be integers or slices, not NoneType".
            prim_exts = getattr(prim, "extensions", None) or {}
            if "KHR_draco_mesh_compression" in prim_exts:
                continue
            pos_acc = gltf.accessors[pos_idx]
            if pos_acc.bufferView is None:
                continue
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
                if n_acc.bufferView is not None:
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
    roughness: float = 0.35,
    metallic: float = 0.05,
    double_sided: bool = True,
) -> None:
    """Inject a PBR material into a GLB using pygltflib.

    Replaces the legacy raw-byte struct manipulation with the same library
    used elsewhere in the pipeline, ensuring consistent and spec-safe output.
    """
    from pygltflib import GLTF2, Material, PbrMetallicRoughness

    gltf = GLTF2.load(glb_path)
    if gltf.materials is None:
        gltf.materials = []
    new_index = len(gltf.materials)
    gltf.materials.append(
        Material(
            name="AcademicAR_Default",
            pbrMetallicRoughness=PbrMetallicRoughness(
                baseColorFactor=[float(c) for c in base_color_rgba],
                metallicFactor=float(metallic),
                roughnessFactor=float(roughness),
            ),
            doubleSided=bool(double_sided),
        )
    )
    for mesh_def in gltf.meshes or []:
        for prim in mesh_def.primitives or []:
            prim.material = new_index
    gltf.save(glb_path)


def load_stl_mesh_without_normals(file_path: str) -> trimesh.Trimesh:
    """Load ASCII or binary STL without passing face normals to old trimesh."""
    with open(file_path, "rb") as file:
        header = file.read(512)

    if header.lower().lstrip().startswith(b"solid") and (
        b"facet" in header.lower() or b"endsolid" in header.lower()
    ):
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
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

    def convert(
        self,
        input_path: str,
        output_path: str,
        color: str = None,
        source_unit: str = "auto",
    ) -> bool:
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

            # SEC-6/PERF-3: reject meshes too large to process safely.
            n_faces = len(mesh.faces)
            n_verts = len(mesh.vertices)
            if (MAX_MESH_FACES and n_faces > MAX_MESH_FACES) or (
                MAX_MESH_VERTICES and n_verts > MAX_MESH_VERTICES
            ):
                self.handle_error(
                    f"Model is too complex to process ({n_faces:,} triangles, "
                    f"{n_verts:,} vertices). The limit is {MAX_MESH_FACES:,} triangles "
                    f"and {MAX_MESH_VERTICES:,} vertices. Please simplify/decimate the "
                    f"mesh and upload again."
                )
                return False

            # Apply basis correction once (Z-up -> Y-up) directly to vertices
            basis_correction = trimesh.transformations.rotation_matrix(
                angle=np.radians(-90), direction=[1, 0, 0]
            )
            mesh.apply_transform(basis_correction)
            self.log_operation("Applied basis correction: -90° around X (Z-up to Y-up)")

            # STL files are unitless. We no longer expose source-unit controls
            # in the UI, so the default preserves the uploaded model scale.
            # Legacy job payloads that explicitly pass auto/mm/cm still work.
            raw_extents = np.ptp(mesh.bounds, axis=0)
            max_extent_raw = float(raw_extents.max())
            self.log_operation(
                f"Raw STL extents (unitless): x={raw_extents[0]:.3f}, "
                f"y={raw_extents[1]:.3f}, z={raw_extents[2]:.3f}"
            )

            # Explicit user override beats heuristic. The form sends mm/cm/m;
            # anything else (including "auto") falls back to the heuristic.
            #
            # Heuristic bands (based on real-world model survey):
            #   >1000  → likely microns (µm), e.g. microscopy data
            #   >100   → likely mm, e.g. anatomical models (skull ~200mm)
            #   >1     → likely cm, e.g. tabletop objects (box ~30cm)
            #   ≤1     → likely already meters
            # Target: the final mesh should be in meters for glTF/AR.
            explicit_units = {"mm": 0.001, "cm": 0.01, "m": 1.0}
            if source_unit in explicit_units:
                unit_scale = explicit_units[source_unit]
                unit_label = f"{source_unit} (user-specified)"
            elif max_extent_raw > 1000.0:
                unit_scale, unit_label = 0.000001, "µm (auto-detected)"
            elif max_extent_raw > 100.0:
                unit_scale, unit_label = 0.001, "mm (auto-detected)"
            elif max_extent_raw > 1.0:
                unit_scale, unit_label = 0.01, "cm (auto-detected)"
            else:
                unit_scale, unit_label = 1.0, "m (auto-detected)"

            mesh.apply_scale(unit_scale)
            scaled_max = max_extent_raw * unit_scale

            # Safety clamp: if the scaled model is still unreasonably large
            # (>10m in any dimension), scale it down to fit 2m — prevents
            # absurdly large models from breaking AR placement.
            AR_MAX_EXTENT_M = float(os.environ.get("AR_MAX_EXTENT_M", "2.0"))
            if scaled_max > AR_MAX_EXTENT_M:
                clamp_factor = AR_MAX_EXTENT_M / scaled_max
                mesh.apply_scale(clamp_factor)
                self.log_operation(
                    f"Safety clamp: scaled model ({scaled_max:.2f}m) exceeded "
                    f"{AR_MAX_EXTENT_M}m; applied additional {clamp_factor:.4f}x"
                )
                scaled_max = AR_MAX_EXTENT_M

            self.log_operation(
                f"Source unit: {unit_label} (raw max extent {max_extent_raw:.2f}); "
                f"applied scale {unit_scale} -> {scaled_max * 100:.1f} cm in AR"
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
            try:
                enriched = enrich_glb_for_ar(output_path, target_color)
                if enriched:
                    self.log_operation(
                        f"Enriched GLB: PBR material "
                        f"baseColorFactor={target_color} (roughness=0.35, "
                        f"metallic=0.05, doubleSided=true) + triplanar UVs"
                    )
                else:
                    inject_pbr_material(output_path, target_color)
                    self.log_operation(
                        f"Injected PBR material "
                        f"baseColorFactor={target_color}"
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
