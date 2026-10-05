"""CT/MR DICOM series -> threshold mask on a regular voxel grid.

Only geometry and pixel data are read; patient/study identifiers never leave
this module (they are neither logged nor copied into notes or errors).
"""

from __future__ import annotations

import math

import numpy as np

from .common import MEDICAL_PRESETS, MedicalError, max_voxels
from .meshing import Grid, LayerSource, Source, choose_strides, fmt_mm

_PREFERRED_MODALITIES = ("CT", "MR")
_MIN_SLICES = 3
_MAX_TILT_DEG = 1.0
_NO_SLICES = "No readable DICOM image slices were found in this ZIP."
_COMPRESSED = "This series uses compressed DICOM (JPEG/JPEG 2000). Export it uncompressed and upload again."
_MULTIFRAME = (
    "This series is stored as a multi-frame (enhanced) DICOM. "
    "Export it as a classic series with one file per slice and upload again."
)


def _header(path):
    import pydicom

    try:
        ds = pydicom.dcmread(path, force=True, stop_before_pixels=True)
    except Exception:
        return None
    # force=True parses almost anything, so require the basics of an image slice.
    if not all(k in ds for k in ("SeriesInstanceUID", "Rows", "Columns")):
        return None
    ts = getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", None)
    return {
        "path": path,
        "series": str(ds.SeriesInstanceUID),
        "modality": str(getattr(ds, "Modality", "") or ""),
        "frames": int(getattr(ds, "NumberOfFrames", 1) or 1),
        "compressed": bool(ts is not None and ts.is_compressed),
        "rows": int(ds.Rows),
        "cols": int(ds.Columns),
        "spacing": _floats(getattr(ds, "PixelSpacing", None), 2),
        "iop": _floats(getattr(ds, "ImageOrientationPatient", None), 6),
        "ipp": _floats(getattr(ds, "ImagePositionPatient", None), 3),
        "samples": int(getattr(ds, "SamplesPerPixel", 1) or 1),
        "slope": float(getattr(ds, "RescaleSlope", 1) or 1),
        "intercept": float(getattr(ds, "RescaleIntercept", 0) or 0),
    }


def _floats(value, n):
    try:
        arr = np.array([float(v) for v in value], dtype=np.float64)
    except (TypeError, ValueError):
        return None
    return arr if arr.shape == (n,) else None


def _pick_series(headers):
    groups: dict[str, list] = {}
    for h in headers:
        groups.setdefault(h["series"], []).append(h)

    def rank(group):
        weight = sum(h["frames"] for h in group)
        return (group[0]["modality"] in _PREFERRED_MODALITIES, weight)

    return max(groups.values(), key=rank)


def _read_slice(path, as_int):
    import pydicom
    from pydicom.uid import ImplicitVRLittleEndian

    try:
        ds = pydicom.dcmread(path, force=True)
        if getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", None) is None:
            ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
        arr = ds.pixel_array
    except Exception:
        raise MedicalError("A slice of this series could not be decoded. Export the series again and retry.")
    hu = arr.astype(np.float32)
    slope = float(getattr(ds, "RescaleSlope", 1) or 1)
    intercept = float(getattr(ds, "RescaleIntercept", 0) or 0)
    if slope != 1.0:
        hu *= slope
    if intercept != 0.0:
        hu += intercept
    if as_int:
        return np.clip(np.rint(hu), -32768, 32767).astype(np.int16)
    return hu


def load_series(paths, preset: str) -> Source:
    headers = [h for h in (_header(p) for p in paths) if h]
    if not headers:
        raise MedicalError(_NO_SLICES)
    group = _pick_series(headers)
    if any(h["compressed"] for h in group):
        raise MedicalError(_COMPRESSED)
    if any(h["frames"] > 1 for h in group):
        raise MedicalError(_MULTIFRAME)
    if any(h["samples"] != 1 for h in group):
        raise MedicalError("Colour (RGB) DICOM images are not supported; upload a CT or MR series.")
    if any(h["spacing"] is None or h["iop"] is None or h["ipp"] is None for h in group):
        raise MedicalError("This series is missing slice geometry (pixel spacing or position), so it cannot be rebuilt in 3D.")

    first = group[0]
    for h in group[1:]:
        if h["rows"] != first["rows"] or h["cols"] != first["cols"]:
            raise MedicalError("The slices in this series have different sizes.")
        if not np.allclose(h["spacing"], first["spacing"], atol=1e-3):
            raise MedicalError("The slices in this series have different pixel spacing.")
        if not np.allclose(h["iop"], first["iop"], atol=1e-3):
            raise MedicalError("The slices in this series have different orientations.")

    row_dir, col_dir = first["iop"][:3], first["iop"][3:]
    normal = np.cross(row_dir, col_dir)
    normal /= np.linalg.norm(normal) or 1.0
    group.sort(key=lambda h: float(np.dot(h["ipp"], normal)))
    # Duplicate instances at the same position would read as zero-thickness slices.
    unique = [group[0]]
    for h in group[1:]:
        if abs(np.dot(h["ipp"], normal) - np.dot(unique[-1]["ipp"], normal)) > 1e-3:
            unique.append(h)
    group = unique
    if len(group) < _MIN_SLICES:
        raise MedicalError(f"At least {_MIN_SLICES} slices are needed to build a 3D model; this series has {len(group)}.")

    positions = np.array([h["ipp"] for h in group])
    steps = np.diff(positions, axis=0)
    along = steps @ normal
    dz = float(np.median(along))
    if dz <= 0:
        raise MedicalError("The slice positions in this series could not be ordered.")
    if np.max(along) > 1.5 * dz:
        raise MedicalError("Some slices are missing from this series.")
    cos = np.abs(along) / np.maximum(np.linalg.norm(steps, axis=1), 1e-9)
    if np.min(cos) < math.cos(math.radians(_MAX_TILT_DEG)):
        raise MedicalError("This series has a gantry tilt (slices are not perpendicular to the stack), which is not supported yet. Export a tilt-corrected series.")

    modality = first["modality"] or None
    rows, cols, n = first["rows"], first["cols"], len(group)
    ps = first["spacing"]  # (row spacing, column spacing)
    affine = np.eye(4)
    affine[:3, 0] = row_dir * ps[1]
    affine[:3, 1] = col_dir * ps[0]
    affine[:3, 2] = normal * dz
    affine[:3, 3] = positions[0]

    # Voxel cap: stride the grid while reading, slice by slice, so the full
    # volume never has to fit in memory.
    si, sj, sk = choose_strides((cols, rows, n), np.linalg.norm(affine[:3, :3], axis=0), max_voxels())
    notes = []
    if (si, sj, sk) != (1, 1, 1):
        affine[:3, 0] *= si
        affine[:3, 1] *= sj
        affine[:3, 2] *= sk
        notes.append(f"Downsampled to {fmt_mm(np.linalg.norm(affine[:3, :3], axis=0))} voxels to fit processing limits.")

    chosen = group[::sk]
    as_int = modality == "CT" and all(
        h["slope"] == int(h["slope"]) and h["intercept"] == int(h["intercept"]) for h in chosen
    )
    shape = (math.ceil(cols / si), math.ceil(rows / sj), len(chosen))
    volume = np.empty(shape, dtype=np.int16 if as_int else np.float32)
    for k, h in enumerate(chosen):
        volume[:, :, k] = _read_slice(h["path"], as_int)[::sj, ::si].T

    preset_info, preset_notes = _resolve_preset(preset, modality)
    notes.extend(preset_notes)
    layer = _threshold_layer(volume, preset_info)
    return Source(
        grid=Grid(shape=shape, affine=affine, frame="LPS"),
        layers=[layer],
        notes=notes,
        empty_error="No structure matched the selected preset in this scan.",
        modality=modality,
    )


def _resolve_preset(preset: str, modality):
    info = MEDICAL_PRESETS[preset]
    if info["modality"] == "CT" and modality != "CT":
        shown = modality or "unknown"
        return MEDICAL_PRESETS["auto"], [
            f"This scan is not CT ({shown}) and has no Hounsfield units, so the automatic threshold "
            f"was used instead of the {info['label']} preset."
        ]
    return info, []


def _threshold_layer(volume: np.ndarray, preset_info: dict) -> LayerSource:
    from skimage.filters import threshold_otsu

    cache: dict = {}

    def load():
        if "mask" not in cache:
            if preset_info["mode"] == "otsu":
                threshold = float(threshold_otsu(volume))
            else:
                threshold = preset_info["threshold_hu"]
            mask = volume >= threshold if preset_info["mode"] == "min" else volume > threshold
            if preset_info is MEDICAL_PRESETS["skin"]:
                # Fill the lungs / airways per axial slice so only the outer skin surface is meshed.
                from scipy import ndimage

                for k in range(mask.shape[2]):
                    mask[:, :, k] = ndimage.binary_fill_holes(mask[:, :, k])
            cache["mask"] = _clean(mask)
        return cache["mask"]

    return LayerSource(name=preset_info["label"], color=preset_info["color"], load=load)


def _clean(mask: np.ndarray) -> np.ndarray:
    """Keep components of at least max(100 voxels, 0.5% of the largest) (26-connectivity)."""
    from scipy import ndimage

    labels, count = ndimage.label(mask, structure=np.ones((3, 3, 3), dtype=bool))
    if count == 0:
        return mask
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    keep = sizes >= max(100, 0.005 * sizes.max())
    keep[0] = False
    return keep[labels]
