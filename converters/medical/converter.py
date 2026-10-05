"""MedicalConverter: DICOM series / segmentation -> layered GLB, run in a child process."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..base_converter import BaseConverter
from . import progress
from .common import MedicalError, convert_timeout, parse_presets

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TIMEOUT_MESSAGE = "Processing this scan took too long. Try a smaller series or crop it."
_CRASH_MESSAGE = "The scan could not be processed (out of memory or unreadable data)."


class MedicalConverter(BaseConverter):
    def __init__(self, kind: str, preset: str | None = None):
        super().__init__()
        self.kind = kind
        self.preset = preset
        self.layers: list[dict] = []  # [{"name", "color", "volume_ml"}] on success
        self.notes: list[str] = []  # user-facing notes (downsampling, preset fallback, ...)
        self.modality: str | None = None
        self.voxel_mm: list[float] | None = None

    def convert(
        self,
        input_path: str,
        output_path: str,
        color: str | None = None,
        source_unit: str = "embedded",
        **_: object,
    ) -> bool:
        """Convert the upload to a GLB with one named material per structure.

        ``color`` and ``source_unit`` are accepted for interface parity and ignored:
        layer colours come from the preset / segmentation and units are embedded
        in the scan geometry.
        """
        self.layers, self.notes = [], []
        self.prepare(input_path)
        self.update_status("CONVERTING")
        if self.kind not in ("dicom", "segmentation"):
            self.handle_error("Unsupported medical upload type.")
            return False
        if self.kind == "dicom":
            try:
                parse_presets(self.preset)  # comma-separated list, e.g. "bone,skin"
            except MedicalError as exc:
                self.handle_error(str(exc))
                return False
        if not os.path.isfile(input_path):
            self.handle_error("File not found.")
            return False

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(output_path)), prefix=".medical_result_") as tmp:
            result_path = os.path.join(tmp, "result.json")
            progress_path = os.path.join(tmp, "progress.json")
            command = [
                sys.executable, "-m", "converters.medical.cli", self.kind, input_path, output_path,
                "--preset", self.preset or "", "--result", result_path, "--workdir", tmp,
                "--progress", progress_path,
            ]
            try:
                # The child reports real counts (slices read, structures meshed) into
                # progress_path; the poller hands them to progress_callback while it runs.
                with self._follow_progress(progress_path):
                    proc = subprocess.run(
                        command, cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=convert_timeout()
                    )
            except subprocess.TimeoutExpired:
                self._fail(_TIMEOUT_MESSAGE, output_path)
                return False
            except OSError:
                logger.exception("Could not start the medical conversion process")
                self._fail(_CRASH_MESSAGE, output_path)
                return False

            result = None
            try:
                with open(result_path, encoding="utf-8") as fh:
                    result = json.load(fh)
            except (OSError, ValueError):
                pass

        if result is None:
            # Killed (OOM) or crashed before writing a result; keep the details in the log only.
            tail = (proc.stderr or "").strip().splitlines()[-8:]
            logger.error("Medical conversion exited with code %s: %s", proc.returncode, " | ".join(tail))
            self._fail(_CRASH_MESSAGE, output_path)
            return False
        if not result.get("ok") or not os.path.exists(output_path):
            self._fail(result.get("error") or _CRASH_MESSAGE, output_path)
            return False

        self.layers = result.get("layers", [])
        self.notes = result.get("notes", [])
        self.modality = result.get("modality")
        self.voxel_mm = result.get("voxel_mm")
        self.log_operation(f"Medical conversion produced {len(self.layers)} layer(s)")
        self.cleanup()
        return True

    def _follow_progress(self, progress_path: str):
        if self.progress_callback is None:
            return contextlib.nullcontext()
        return progress.Poller(progress_path, self.progress_callback)

    def _fail(self, message: str, output_path: str) -> None:
        self.handle_error(message)
        try:
            os.remove(output_path)  # drop a partial GLB from a killed child
        except OSError:
            pass
