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


def inject_pbr_material(
    glb_path: str,
    base_color_rgba: tuple,
    roughness: float = 0.85,
    metallic: float = 0.0,
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
            target_color = (0.8, 0.8, 0.8, 1.0)  # default light gray
            if color:
                try:
                    hex_color = color.lstrip("#")
                    if len(hex_color) != 6:
                        raise ValueError(f"Invalid hex color: {color}")
                    target_color = (
                        int(hex_color[0:2], 16) / 255.0,
                        int(hex_color[2:4], 16) / 255.0,
                        int(hex_color[4:6], 16) / 255.0,
                        1.0,
                    )
                    self.log_operation(
                        f"Target PBR color parsed from '{color}': {target_color}"
                    )
                except Exception as e:
                    self.log_operation(
                        f"Warning: could not parse color '{color}': {e}; "
                        f"falling back to default light gray",
                        "WARNING",
                    )
            else:
                self.log_operation(
                    "No color specified; using default light gray for PBR material"
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

            # Post-process: inject a PBR material with baseColorFactor + doubleSided
            # so AR engines (iOS Quick Look, Android Scene Viewer) render the chosen
            # color and don't drop back faces on non-watertight anatomical meshes.
            try:
                inject_pbr_material(output_path, target_color)
                self.log_operation(
                    f"Injected PBR material baseColorFactor={target_color} "
                    f"(roughness=0.85, metallic=0.0, doubleSided=true)"
                )
            except Exception as e:
                self.log_operation(
                    f"Warning: PBR material injection failed: {e}; "
                    f"GLB will render with default white in AR.",
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
