"""External CLI converters for OBJ/FBX to GLB.

These wrappers intentionally stay small: AcademicAR owns validation, storage,
licensing, and job orchestration; the heavy format translation is delegated to
the proven Node CLIs used by the older web_ar project.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .base_converter import BaseConverter
from .stl_converter import enrich_glb_for_ar, inject_pbr_material


class ExternalConverter(BaseConverter):
    extension: str = ""
    command_env: str = ""
    default_command: tuple[str, ...] = ()

    def validate(self, file_path: str) -> bool:
        if not super().validate(file_path):
            return False
        if Path(file_path).suffix.lower() != self.extension:
            self.handle_error(f"Unsupported file format: {Path(file_path).suffix}")
            return False
        if os.path.getsize(file_path) <= 0:
            self.handle_error("File is empty.")
            return False
        return True

    def _command(self) -> list[str]:
        override = os.environ.get(self.command_env, "").strip()
        if override:
            return shlex.split(override)
        cmd = list(self.default_command)
        if os.name == "nt" and cmd and cmd[0] == "npx":
            cmd[0] = "npx.cmd"
        return cmd

    def _run(self, command: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=int(os.environ.get("MODEL_CONVERT_TIMEOUT", "300")),
            )
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(command, 127, "", str(exc))
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(command, 124, exc.stdout or "", exc.stderr or "Conversion timed out.")

    def _post_process_glb(self, output_path: str, color: str | None) -> None:
        if not color:
            return
        hex_color = color.strip()
        if not hex_color.startswith("#") or len(hex_color) != 7:
            return
        try:
            r = int(hex_color[1:3], 16) / 255.0
            g = int(hex_color[3:5], 16) / 255.0
            b = int(hex_color[5:7], 16) / 255.0
        except ValueError:
            return
        rgba = (r, g, b, 1.0)
        if not enrich_glb_for_ar(output_path, rgba):
            inject_pbr_material(output_path, rgba)


class OBJConverter(ExternalConverter):
    extension = ".obj"
    command_env = "OBJ2GLTF_COMMAND"
    default_command = ("npx", "obj2gltf")

    def convert(self, input_path: str, output_path: str, color: str | None = None, **_: object) -> bool:
        if not self.validate(input_path):
            return False
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        command = self._command() + [
            "-i",
            input_path,
            "-o",
            output_path,
            "--binary",
            "--checkTransparency",
        ]
        result = self._run(command, cwd=os.path.dirname(input_path))
        if result.returncode != 0 or not os.path.exists(output_path):
            self.handle_error(result.stderr or result.stdout or "OBJ to GLB conversion failed.")
            return False
        self._post_process_glb(output_path, color)
        return True


class FBXConverter(ExternalConverter):
    extension = ".fbx"
    command_env = "FBX2GLTF_COMMAND"
    default_command = ("npx", "fbx2gltf")

    def convert(self, input_path: str, output_path: str, color: str | None = None, **_: object) -> bool:
        if not self.validate(input_path):
            return False
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        command = self._command() + [
            "-i",
            input_path,
            "-o",
            output_path,
            "-b",
        ]
        result = self._run(command, cwd=os.path.dirname(input_path))
        produced = Path(output_path)
        if not produced.exists():
            candidates = sorted(Path(os.path.dirname(output_path)).glob("*.glb"))
            if candidates:
                shutil.move(str(candidates[0]), output_path)
        if result.returncode != 0 or not os.path.exists(output_path):
            self.handle_error(result.stderr or result.stdout or "FBX to GLB conversion failed.")
            return False
        self._post_process_glb(output_path, color)
        return True
