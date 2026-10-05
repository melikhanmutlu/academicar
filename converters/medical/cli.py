"""Child-process entry point: ``python -m converters.medical.cli <kind> <input> <output> ...``.

The heavy lifting (decoding, meshing) runs here so an out-of-memory kill or a
crash only takes down this process, never the worker that also serves the web
app. Results go to a small JSON file; nothing patient-related is ever written
to it, to stdout or to the logs.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import warnings

from .common import MEDICAL_PRESETS, MedicalError, safe_extract

logger = logging.getLogger(__name__)

_UNEXPECTED = "The scan could not be processed (out of memory or unreadable data)."


def run_conversion(kind: str, input_path: str, output_path: str, preset: str | None = None, workdir_root: str | None = None) -> dict:
    """Convert one upload to a layered GLB. Returns the result dict (never raises on bad input)."""
    from .dicom_series import load_series
    from .meshing import build_glb
    from .segmentation import load_segmentation

    result = {"ok": False, "error": None, "layers": [], "notes": [], "modality": None, "voxel_mm": None}
    out_dir = os.path.dirname(os.path.abspath(output_path))
    try:
        os.makedirs(out_dir, exist_ok=True)
        # Scratch space next to the output (same volume as the stored files), always removed.
        # The parent may pass its own directory so a killed child leaves nothing behind.
        with tempfile.TemporaryDirectory(dir=workdir_root or out_dir, prefix=".medical_") as workdir:
            if kind == "dicom":
                preset = preset or "auto"
                if preset not in MEDICAL_PRESETS:
                    raise MedicalError("Unknown preset.")
                if not _is_zip(input_path):
                    raise MedicalError("Upload the whole DICOM series as a ZIP file (a single .dcm file holds only one slice).")
                source = load_series(safe_extract(input_path, workdir), preset)
            elif kind == "segmentation":
                source = load_segmentation(input_path, workdir)
            else:
                raise MedicalError("Unsupported medical upload type.")
            layers, notes, voxel_mm = build_glb(source, output_path)
        result.update(ok=True, layers=layers, notes=notes, modality=source.modality, voxel_mm=voxel_mm)
    except MedicalError as exc:
        result["error"] = str(exc)
    except MemoryError:
        result["error"] = _UNEXPECTED
    except Exception as exc:
        # Log only the exception type: messages from pydicom/nibabel can echo header values.
        logger.error("Medical conversion failed (%s)", type(exc).__name__)
        result["error"] = _UNEXPECTED
    if not result["ok"] and os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass
    return result


def _is_zip(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"PK"
    except OSError:
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="converters.medical.cli")
    parser.add_argument("kind", choices=["dicom", "segmentation"])
    parser.add_argument("input_path")
    parser.add_argument("output_path")
    parser.add_argument("--preset", default="")
    parser.add_argument("--result", required=True)
    parser.add_argument("--workdir", default="")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    # pydicom warnings can quote header values (which may be patient data); keep stderr clean.
    warnings.simplefilter("ignore")
    logging.getLogger("pydicom").setLevel(logging.CRITICAL)
    result = run_conversion(args.kind, args.input_path, args.output_path, args.preset or None, args.workdir or None)
    with open(args.result, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
