"""CT/MR DICOM series -> threshold mask on a regular voxel grid.

Only geometry and pixel data are read; patient/study identifiers never leave
this module (they are neither logged nor copied into notes or errors).
"""

from __future__ import annotations

import math

import numpy as np

from .common import MEDICAL_PRESETS, MedicalError, max_voxels, parse_presets
from .meshing import Grid, LayerSource, Source, choose_strides, fmt_mm
from .segmentation import _load_dicom_seg

_PREFERRED_MODALITIES = ("CT", "MR")
_MIN_SLICES = 3
_MAX_TILT_DEG = 1.0
_NO_SLICES = "No readable DICOM image slices were found in this ZIP."
# Compressed series (JPEG Lossless, JPEG-LS, JPEG 2000, RLE) decode through
# the GDCM plugin (python-gdcm); this message is only for the rare syntaxes it
# cannot read (e.g. 12-bit JPEG Extended).
_COMPRESSED = (
    "This series uses a DICOM compression that cannot be decoded ({name}). "
    "Export it uncompressed and upload again."
)
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
    except Exception:
        raise MedicalError("A slice of this series could not be decoded. Export the series again and retry.")
    try:
        arr = ds.pixel_array
    except Exception:
        ts = ds.file_meta.TransferSyntaxUID
        if ts.is_compressed:
            raise MedicalError(_COMPRESSED.format(name=ts.name))
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


def _use_segmentation(segs):
    """Source for the DICOM-SEG with the most frames, or (None, note) when it cannot be read."""
    best = max(segs, key=lambda h: h["frames"])
    try:
        source = _load_dicom_seg(best["path"])
    except MedicalError as exc:
        reason = str(exc).rstrip(".")
        return None, f"The DICOM segmentation in this ZIP could not be used ({reason}); a threshold was used instead."
    source.notes.append("Used the DICOM segmentation found in the ZIP instead of a threshold.")
    if len(segs) > 1:
        source.notes.append(f"This ZIP has {len(segs)} DICOM segmentations; used the one with the most frames.")
    return source, None


def load_series(paths, preset: str) -> Source:
    """``preset`` is one preset key or a comma-separated list ("bone,skin"); each becomes a layer."""
    presets = parse_presets(preset)
    headers = [h for h in (_header(p) for p in paths) if h]
    notes = []
    # A DICOM-SEG exported next to the CT (3D Slicer, OHIF) is what the user wants to see.
    segs = [h for h in headers if h["modality"] == "SEG"]
    headers = [h for h in headers if h["modality"] != "SEG"]
    if segs:
        source, note = _use_segmentation(segs)
        if source is not None:
            return source
        notes.append(note)
    if not headers:
        raise MedicalError(_NO_SLICES)
    group = _pick_series(headers)
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
    series_count = len({h["series"] for h in headers})
    if series_count > 1:  # counts only: no series descriptions or UIDs
        notes.append(f"This ZIP has {series_count} image series; used the one with {len(group)} slices.")

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

    infos, preset_notes = _resolve_presets(presets, modality)
    notes.extend(preset_notes)
    return Source(
        grid=Grid(shape=shape, affine=affine, frame="LPS"),
        layers=_threshold_layers(volume, infos),
        notes=notes,
        empty_error="No structure matched the selected preset in this scan.",
        modality=modality,
    )


def _resolve_presets(presets, modality):
    """Preset keys to build, in order. Without Hounsfield units (not CT) every HU preset becomes "auto"."""
    keys, fallback = [], []
    for key in presets:
        info = MEDICAL_PRESETS[key]
        if info["modality"] == "CT" and modality != "CT":
            fallback.append(info["label"])
            key = "auto"
        if key not in keys:
            keys.append(key)
    notes = []
    if fallback:
        shown = modality or "unknown"
        plural = "s" if len(fallback) > 1 else ""
        notes.append(
            f"This scan is not CT ({shown}) and has no Hounsfield units, so the automatic threshold "
            f"was used instead of the {', '.join(fallback)} preset{plural}."
        )
    return keys, notes


def _threshold_layers(volume: np.ndarray, keys) -> list:
    from scipy import ndimage
    from skimage.filters import threshold_otsu

    cache: dict = {}

    def mask_for(key):
        if key not in cache:
            info = MEDICAL_PRESETS[key]
            if info["mode"] == "otsu":
                threshold = float(threshold_otsu(volume))
            else:
                threshold = info["threshold_hu"]
            mask = volume >= threshold if info["mode"] == "min" else volume > threshold
            if key == "contrast" and "bone" in keys:
                # Bone (plus one voxel of partial-volume rim) belongs to the Bone layer only.
                mask &= ~ndimage.binary_dilation(mask_for("bone"))
            if key == "skin":
                # Fill the lungs / airways per axial slice so only the outer skin surface is meshed.
                for k in range(mask.shape[2]):
                    mask[:, :, k] = ndimage.binary_fill_holes(mask[:, :, k])
                mask = _largest_component(mask)  # the body, not the scanner table
            else:
                mask = _clean(mask)
            cache[key] = mask
        return cache[key]

    return [
        LayerSource(name=MEDICAL_PRESETS[k]["label"], color=MEDICAL_PRESETS[k]["color"], load=lambda k=k: mask_for(k))
        for k in keys
    ]


def _largest_component(mask: np.ndarray) -> np.ndarray:
    """Keep only the biggest connected component (26-connectivity)."""
    from scipy import ndimage

    labels, count = ndimage.label(mask, structure=np.ones((3, 3, 3), dtype=bool))
    if count == 0:
        return mask
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    return labels == sizes.argmax()


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
