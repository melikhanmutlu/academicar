"""STEP (.step/.stp) to GLB conversion via OpenCascade (cascadio).

OpenCascade runs in a child process (converters/step_cli.py) so a crash or
memory blow-up cannot take the worker down. cascadio writes metres, Z-up, with
one node per part and one material per STEP colour; this module fixes the up
axis, caps the triangle count and makes sure every primitive has a material.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from pygltflib import GLTF2, Node, Scene

from .base_converter import BaseConverter
from .external_converter import _safe_timeout
from .layers import default_material
from .stl_converter import _srgb_to_linear

logger = logging.getLogger(__name__)

# Tessellation tolerances, in STEP model millimetres (OpenCascade normalises
# every unit to mm, whatever the file declares). 0.05 mm / ~11 degrees keeps
# holes and fillets of small mechanical parts smooth while large parts stay
# bounded by the angular limit. Overridable via env.
DEFAULT_TOL_LINEAR = 0.05
DEFAULT_TOL_ANGULAR = 0.2
DEFAULT_MAX_TRIANGLES = 3_000_000

# -90 degrees about X (glTF x, y, z, w): STEP is Z-up, glTF/model-viewer Y-up.
Z_UP_TO_Y_UP = [-0.70710678, 0.0, 0.0, 0.70710678]


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def _count_triangles(gltf: GLTF2) -> int:
    total = 0
    for mesh in gltf.meshes or []:
        for primitive in mesh.primitives or []:
            if primitive.mode not in (None, 4):  # triangles only
                continue
            source = primitive.indices if primitive.indices is not None else primitive.attributes.POSITION
            if source is not None:
                total += gltf.accessors[source].count // 3
    return total


class STEPConverter(BaseConverter):
    extensions = (".step", ".stp")

    def validate(self, file_path: str) -> bool:
        if not super().validate(file_path):
            return False
        if Path(file_path).suffix.lower() not in self.extensions:
            self.handle_error(f"Unsupported file format: {Path(file_path).suffix}")
            return False
        if os.path.getsize(file_path) <= 0:
            self.handle_error("File is empty.")
            return False
        with open(file_path, "rb") as handle:
            head = handle.read(2048)
        if head.startswith(b"\xef\xbb\xbf"):
            head = head[3:]
        if not head.lstrip().startswith(b"ISO-10303-21"):
            self.handle_error("This file is not a valid STEP file (missing ISO-10303-21 header).")
            return False
        return True

    def convert(self, input_path: str, output_path: str, color: str | None = None, source_unit: str = "embedded", **_: object) -> bool:
        # source_unit is ignored: STEP embeds its unit and cascadio emits metres.
        if not self.validate(input_path):
            return False
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        # Convert beside the target and move into place only on success, so a
        # failed run never leaves (or mistakes) a stale output_path.
        work_path = output_path + ".step-tmp.glb"  # extension decides GLB vs glTF
        try:
            if not self._run_cascadio(input_path, work_path):
                return False
            if not self._post_process(work_path, color):
                return False
            os.replace(work_path, output_path)
            return True
        finally:
            if os.path.exists(work_path):
                os.remove(work_path)

    def _run_cascadio(self, input_path: str, output_path: str) -> bool:
        command = [
            sys.executable,
            "-m",
            "converters.step_cli",
            input_path,
            output_path,
            "--tol-linear",
            str(_env_float("STEP_TOL_LINEAR", DEFAULT_TOL_LINEAR)),
            "--tol-angular",
            str(_env_float("STEP_TOL_ANGULAR", DEFAULT_TOL_ANGULAR)),
        ]
        try:
            result = subprocess.run(
                command,
                cwd=str(Path(__file__).parent.parent),
                capture_output=True,
                text=True,
                timeout=_safe_timeout(),
            )
        except subprocess.TimeoutExpired:
            self.handle_error("STEP conversion timed out. Try simplifying the assembly or exporting fewer parts.")
            return False
        except FileNotFoundError:
            self.handle_error("STEP conversion is not available on this server.")
            return False
        if result.returncode != 0 or not os.path.exists(output_path):
            logger.warning("STEP conversion failed (exit %s): %s", result.returncode, (result.stderr or "")[-500:])
            self.handle_error("STEP conversion failed. The file may be corrupt or use unsupported STEP features.")
            return False
        return True

    def _post_process(self, glb_path: str, color: str | None) -> bool:
        try:
            gltf = GLTF2.load(glb_path)
        except Exception:
            logger.exception("Could not read cascadio output %s", glb_path)
            self.handle_error("STEP conversion failed. The file may be corrupt or use unsupported STEP features.")
            return False

        triangles = _count_triangles(gltf)
        if triangles == 0:
            self.handle_error("This STEP file contains no solid geometry to display.")
            return False
        max_triangles = _env_int("STEP_MAX_TRIANGLES", DEFAULT_MAX_TRIANGLES)
        if max_triangles and triangles > max_triangles:
            self.handle_error(f"This STEP model is too detailed to display ({triangles:,} triangles). Simplify or export fewer parts.")
            return False

        # Assembly instances come out named after their OCAF label ("=>[0:1:1:2]");
        # the part's real name is on its mesh.
        for node in gltf.nodes or []:
            if node.mesh is not None and (not (node.name or "").strip() or node.name.startswith("=>[")):
                node.name = gltf.meshes[node.mesh].name or node.name

        # Z-up -> Y-up by parenting the scene under a rotated root; part nodes keep their names.
        if not gltf.scenes:
            children = {c for n in gltf.nodes for c in (n.children or [])}
            gltf.scenes = [Scene(nodes=[i for i in range(len(gltf.nodes)) if i not in children])]
            gltf.scene = 0
        scene = gltf.scenes[gltf.scene or 0]
        gltf.nodes.append(Node(name="STEP root", rotation=list(Z_UP_TO_Y_UP), children=list(scene.nodes)))
        scene.nodes = [len(gltf.nodes) - 1]

        # Colour: a STEP's own colours always win; the picker colour only
        # applies when the file carried none.
        if gltf.materials is None:
            gltf.materials = []
        has_own_colour = any(p.material is not None for m in gltf.meshes for p in m.primitives)
        default_index = None
        for mesh in gltf.meshes:
            for primitive in mesh.primitives:
                if primitive.material is None:
                    if default_index is None:
                        gltf.materials.append(default_material("Default"))
                        default_index = len(gltf.materials) - 1
                    primitive.material = default_index
        if color and not has_own_colour:
            rgba = self._parse_color(color)
            if rgba:
                for material in gltf.materials:
                    material.pbrMetallicRoughness.baseColorFactor = rgba

        gltf.save(glb_path)
        return True

    @staticmethod
    def _parse_color(color: str) -> list[float] | None:
        hex_color = color.strip()
        if not hex_color.startswith("#") or len(hex_color) != 7:
            return None
        try:
            r, g, b = (_srgb_to_linear(int(hex_color[i:i + 2], 16) / 255.0) for i in (1, 3, 5))
        except ValueError:
            return None
        return [r, g, b, 1.0]
