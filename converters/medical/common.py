"""Shared constants, limits and the safe ZIP helpers for medical conversion.

Privacy rule for this whole package: DICOM identifiers (PatientName, PatientID,
BirthDate, study/series descriptions, ...) are never logged, copied into notes,
errors or the result JSON. Only geometry, modality and structure names the user
chose in their own segmentation tool leave the readers.
"""

from __future__ import annotations

import os
import stat
import zipfile

from . import progress

# Hard cap on structures per GLB; each one costs a node, a material and a
# toggle in the viewer.
MAX_LAYERS = 64

# Thresholds are in Hounsfield units and only meaningful for CT. "auto" uses an
# Otsu threshold and is the only preset that makes sense for MR (no HU scale).
MEDICAL_PRESETS: dict[str, dict] = {
    "bone": {
        "label": "Bone",
        "description": "Bone and other dense tissue (CT, 250 HU and above).",
        "color": "#E8D5B7",
        "modality": "CT",
        "threshold_hu": 250,
        "mode": "min",
    },
    "skin": {
        "label": "Skin",
        "description": "Outer body surface (CT, -300 HU and above). Only the largest connected piece (the body) is kept.",
        "color": "#FFCBA4",
        "modality": "CT",
        "threshold_hu": -300,
        "mode": "min",
    },
    "contrast": {
        "label": "Contrast vessels",
        "description": (
            "Contrast-filled vessels (CT, 150 HU and above). Bone is included as well because "
            "thresholding cannot separate bright vessels from bone, unless you also choose Bone: "
            "then bone is left out of this layer so the two layers do not overlap."
        ),
        "color": "#CC2222",
        "modality": "CT",
        "threshold_hu": 150,
        "mode": "min",
    },
    "auto": {
        "label": "Auto threshold",
        "description": "Automatic (Otsu) threshold. Works for CT and MR, but separates only two intensity classes.",
        "color": "#CCCCCC",
        "modality": None,
        "threshold_hu": None,
        "mode": "otsu",
    },
}

# Fixed, distinct, pleasant fallback colours for segmentations without colour
# metadata (cycled when there are more labels than colours).
LAYER_PALETTE = [
    "#E4572E", "#4C9F70", "#3A86C8", "#F0B429", "#8E6BBF", "#2BB3B1", "#D96C9E", "#8DA33D",
    "#E07A3F", "#5B7DB1", "#B5835A", "#6FBF73", "#C95D63", "#4FA3A5", "#A68A3A", "#9A7FB8",
    "#D4A5A5", "#7C9C5E", "#5E8CC7", "#C98A4B",
]

UNSAFE_ZIP_MESSAGE = "This ZIP contains unsafe paths."
NOT_ZIP_MESSAGE = "This file is not a valid ZIP archive."
ENCRYPTED_ZIP_MESSAGE = "Password-protected ZIP files are not supported. Remove the password and upload again."
ZIP_TOO_BIG_MESSAGE = "This ZIP is too large to process once unpacked. Try a smaller series or crop it."
ZIP_TOO_MANY_MESSAGE = "This ZIP contains too many files. Try a smaller series."

NIFTI_SUFFIXES = (".nii.gz", ".nii")
NRRD_SUFFIX = ".nrrd"


class MedicalError(Exception):
    """A user-facing problem with the uploaded scan. The message is shown as-is."""


# A user-chosen CT threshold: "custom:<min HU>" or "custom:<min HU>:<max HU>".
CUSTOM_PRESET = "custom"
CUSTOM_HU_MIN = -1024
CUSTOM_HU_MAX = 4000
CUSTOM_COLOR = "#7FB3D5"


def parse_custom_preset(key: str) -> tuple[int, int | None] | None:
    """(min, max or None) of a "custom:..." key, or None when it is not a valid one."""
    parts = str(key).split(":")
    if parts[0] != CUSTOM_PRESET or len(parts) not in (2, 3):
        return None
    try:
        low = int(parts[1])
        high = int(parts[2]) if len(parts) == 3 else None
    except ValueError:
        return None
    if not CUSTOM_HU_MIN <= low <= CUSTOM_HU_MAX:
        return None
    if high is not None and not low < high <= CUSTOM_HU_MAX:
        return None
    return low, high


def custom_preset_key(low: int, high: int | None = None) -> str:
    return f"{CUSTOM_PRESET}:{low}" + (f":{high}" if high is not None else "")


def preset_info(key: str) -> dict:
    """The MEDICAL_PRESETS entry for a key, or one built for a "custom:..." key."""
    if key in MEDICAL_PRESETS:
        return MEDICAL_PRESETS[key]
    bounds = parse_custom_preset(key)
    if bounds is None:
        raise MedicalError("Unknown preset.")
    low, high = bounds
    shown = f"{low} to {high} HU" if high is not None else f"{low} HU and above"
    return {
        "label": f"Custom ({shown})",
        "description": f"User-chosen CT threshold ({shown}).",
        "color": CUSTOM_COLOR,
        "modality": "CT",
        "threshold_hu": low,
        "max_hu": high,
        "mode": "range",
    }


def parse_presets(value) -> list[str]:
    """Preset keys from a comma-separated string ("bone,skin" or "bone,custom:300:900");
    empty -> ["auto"], duplicates dropped."""
    keys: list[str] = []
    for part in str(value or "").split(","):
        key = part.strip().lower()
        if key and key not in keys:
            keys.append(key)
    if any(k not in MEDICAL_PRESETS and parse_custom_preset(k) is None for k in keys):
        raise MedicalError("Unknown preset.")
    return keys or ["auto"]


def _env_int(name: str, default: int) -> int:
    # Read at call time so limits can be tuned (and tested) without a reload.
    try:
        return int(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def max_unzipped_bytes() -> int:
    return _env_int("MEDICAL_MAX_UNZIPPED_BYTES", 2 * 1024**3)


def max_files() -> int:
    return _env_int("MEDICAL_MAX_FILES", 5000)


def max_voxels() -> int:
    return _env_int("MEDICAL_MAX_VOXELS", 40_000_000)


def max_faces() -> int:
    return _env_int("MEDICAL_MAX_FACES", 1_500_000)


def convert_timeout() -> int:
    return _env_int("MEDICAL_CONVERT_TIMEOUT", 900)


def is_segmentation_name(name: str) -> bool:
    return name.lower().endswith(NIFTI_SUFFIXES + (NRRD_SUFFIX,))


def _skipped_member(name: str) -> bool:
    parts = [p for p in name.replace("\\", "/").split("/") if p]
    return any(p == "__MACOSX" or p.startswith(".") for p in parts)


def _unsafe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return True
    if ".." in name.split("/"):
        return True
    return stat.S_ISLNK(info.external_attr >> 16)


def inspect_zip(path: str) -> list[zipfile.ZipInfo]:
    """Open a ZIP and return its usable file members, enforcing the safety limits.

    Checks run on the central directory only (nothing is decompressed), so this
    is cheap enough for the web upload request. Directories and __MACOSX /
    dotfile entries are dropped from the result.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise MedicalError(NOT_ZIP_MESSAGE)
    with zf:
        infos = zf.infolist()
    for info in infos:
        if _unsafe_member(info):
            raise MedicalError(UNSAFE_ZIP_MESSAGE)
    members = [i for i in infos if not i.is_dir() and not _skipped_member(i.filename)]
    if any(i.flag_bits & 0x1 for i in members):
        raise MedicalError(ENCRYPTED_ZIP_MESSAGE)
    if len(members) > max_files():
        raise MedicalError(ZIP_TOO_MANY_MESSAGE)
    if sum(i.file_size for i in members) > max_unzipped_bytes():
        raise MedicalError(ZIP_TOO_BIG_MESSAGE)
    return members


def safe_extract(zip_path: str, dest_dir: str) -> list[str]:
    """Extract the usable members of ``zip_path`` into ``dest_dir``.

    Returns the extracted file paths. Members are written under flattened-safe
    names (path components kept, validated by ``inspect_zip``); the byte cap is
    enforced again while streaming because the declared sizes can lie.
    """
    members = inspect_zip(zip_path)
    limit = max_unzipped_bytes()
    total = 0
    out_paths: list[str] = []
    root = os.path.realpath(dest_dir)
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise MedicalError(NOT_ZIP_MESSAGE)
    with zf:
        for index, info in enumerate(members):
            progress.report(1 + 7 * index / max(1, len(members)), f"Unpacking files {index} / {len(members)}")
            rel = info.filename.replace("\\", "/")
            target = os.path.realpath(os.path.join(root, rel))
            if not target.startswith(root + os.sep):
                raise MedicalError(UNSAFE_ZIP_MESSAGE)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                with zf.open(info) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > limit:
                            raise MedicalError(ZIP_TOO_BIG_MESSAGE)
                        dst.write(chunk)
            except (zipfile.BadZipFile, RuntimeError, OSError, EOFError):
                raise MedicalError("This ZIP could not be unpacked (it may be damaged).")
            out_paths.append(target)
    return out_paths
