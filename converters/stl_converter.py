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

            # Apply color using vertex colors (no UV coordinates needed)
            # STL meshes have no UV data, so TextureVisuals would create phantom TEXCOORD_0
            # causing purple striped texture in AR (iOS Quick Look, Android Scene Viewer)
            # Use ColorVisuals with vertex colors instead
            if color:
                try:
                    self.log_operation(f"Applying color: {color}")
                    # Convert hex color to RGB (0-255 range)
                    hex_color = color.lstrip("#")
                    if len(hex_color) != 6:
                        raise ValueError(f"Invalid hex color: {color}")

                    r = int(hex_color[0:2], 16)
                    g = int(hex_color[2:4], 16)
                    b = int(hex_color[4:6], 16)

                    # Apply vertex colors only (no TextureVisuals, no phantom UV data)
                    if isinstance(mesh, trimesh.Scene):
                        for geom in mesh.geometry.values():
                            if isinstance(geom, trimesh.Trimesh):
                                vertex_colors = np.tile(
                                    [r, g, b, 255], (len(geom.vertices), 1)
                                )
                                geom.visual = trimesh.visual.ColorVisuals(
                                    vertex_colors=vertex_colors.astype(np.uint8)
                                )
                    else:
                        vertex_colors = np.tile([r, g, b, 255], (len(mesh.vertices), 1))
                        mesh.visual = trimesh.visual.ColorVisuals(
                            vertex_colors=vertex_colors.astype(np.uint8)
                        )

                    self.log_operation(
                        f"Color applied successfully: RGB({r}, {g}, {b}) using vertex colors (no UV)"
                    )
                except Exception as e:
                    self.log_operation(
                        f"Warning: Could not apply color: {str(e)}", "WARNING"
                    )
                    import traceback

                    self.log_operation(f"Traceback: {traceback.format_exc()}")
                    # Continue even if color application fails
            else:
                # No color specified - apply default gray using vertex colors
                self.log_operation(
                    "No color specified - applying default gray using vertex colors"
                )
                try:
                    # Default light gray in RGB(255 scale): [204, 204, 204] = 0.8*255
                    default_r, default_g, default_b = 204, 204, 204

                    if isinstance(mesh, trimesh.Scene):
                        for geom in mesh.geometry.values():
                            if isinstance(geom, trimesh.Trimesh):
                                vertex_colors = np.tile(
                                    [default_r, default_g, default_b, 255],
                                    (len(geom.vertices), 1),
                                )
                                geom.visual = trimesh.visual.ColorVisuals(
                                    vertex_colors=vertex_colors.astype(np.uint8)
                                )
                    else:
                        vertex_colors = np.tile(
                            [default_r, default_g, default_b, 255], (len(mesh.vertices), 1)
                        )
                        mesh.visual = trimesh.visual.ColorVisuals(
                            vertex_colors=vertex_colors.astype(np.uint8)
                        )

                    self.log_operation(
                        f"Default gray applied using vertex colors (no UV/texture data)"
                    )
                except Exception as e:
                    self.log_operation(
                        f"Warning: Could not apply default color: {str(e)}",
                        "WARNING",
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
