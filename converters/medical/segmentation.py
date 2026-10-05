"""Existing segmentations (NIfTI, NRRD / Slicer .seg.nrrd, DICOM-SEG, ZIP of masks) -> layers.

Each non-empty label becomes one ``LayerSource``. Masks are loaded lazily, one at
a time, so a TotalSegmentator folder with ~100 structures never sits in memory
at once.
"""

from __future__ import annotations

import os
import re

import numpy as np

from .common import MedicalError, is_segmentation_name, max_voxels, safe_extract
from .meshing import Grid, LayerSource, Source, rgb_to_hex

_NIFTI_SUFFIXES = (".nii.gz", ".nii")
_NOT_SEGMENTATION = "This file does not look like a segmentation (it contains continuous image intensities)."
_UNREADABLE = "This segmentation file could not be read."
_NO_LABELS = "No structures were found in this segmentation."


# ---------------------------------------------------------------- entry points

def sniff(path: str) -> str | None:
    """Identify a segmentation file by content (the stored name may have no extension)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(352)
    except OSError:
        return None
    if head[:2] == b"PK":
        return "zip"
    if head[:4] == b"NRRD":
        return "nrrd"
    if head[:2] == b"\x1f\x8b":
        return "nifti"
    if head[344:348] in (b"n+1\0", b"ni1\0") or head[4:8] == b"n+2\0":
        return "nifti"
    if head[128:132] == b"DICM" or path.lower().endswith(".dcm"):
        return "dicom_seg"
    return None


def load_segmentation(path: str, workdir: str) -> Source:
    kind = sniff(path)
    if kind == "zip":
        return _load_mask_zip(path, workdir)
    if kind == "nifti":
        return _load_nifti(path, workdir)
    if kind == "nrrd":
        return _load_nrrd(path)
    if kind == "dicom_seg":
        return _load_dicom_seg(path)
    raise MedicalError("This file is not a supported segmentation format (NIfTI, NRRD, DICOM-SEG or a ZIP of masks).")


# ------------------------------------------------------------- label handling

def _label_counts(arr: np.ndarray) -> dict:
    """{label value: voxel count} for the non-zero labels of an integer array."""
    if arr.size == 0:
        return {}
    lo, hi = int(arr.min()), int(arr.max())
    if lo >= 0 and hi < (1 << 20):
        total = np.zeros(hi + 1, dtype=np.int64)
        flat = arr.reshape(-1)
        for start in range(0, flat.size, 1 << 24):  # chunked: bincount copies to intp
            total += np.bincount(flat[start : start + (1 << 24)], minlength=hi + 1)
        return {int(v): int(c) for v, c in enumerate(total) if v and c}
    values, counts = np.unique(arr, return_counts=True)
    return {int(v): int(c) for v, c in zip(values, counts) if v}


def _array_layers(arr: np.ndarray) -> list[LayerSource]:
    """Layers for a 3D label map (or binary mask), named "Label N"."""
    if arr.dtype == np.bool_:
        arr = arr.astype(np.uint8)
    if np.issubdtype(arr.dtype, np.floating):
        if not np.isfinite(arr).all():
            raise MedicalError(_NOT_SEGMENTATION)
        if arr.min() >= 0 and arr.max() <= 1.0 + 1e-6:
            # Binary float mask (e.g. a resampled or probability-style mask).
            return [LayerSource("Label 1", None, lambda: arr > 0.5)]
        if not np.array_equal(arr, np.rint(arr)):
            raise MedicalError(_NOT_SEGMENTATION)
        arr = arr.astype(np.int32)
    counts = _label_counts(arr)
    return [
        LayerSource(f"Label {v}", None, (lambda v=v: arr == v), count=c)
        for v, c in sorted(counts.items())
    ]


# ------------------------------------------------------------------ NIfTI

def _nifti_path(path: str, workdir: str) -> str:
    """nibabel picks the reader from the extension; give extension-less uploads a proper name."""
    if path.lower().endswith(_NIFTI_SUFFIXES):
        return path
    with open(path, "rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    link = os.path.join(workdir, "input.nii.gz" if gz else "input.nii")
    try:
        os.link(path, link)
    except OSError:
        import shutil

        shutil.copyfile(path, link)
    return link


def _nifti_geometry(img) -> tuple:
    shape = tuple(img.shape)
    if len(shape) > 3 and all(n == 1 for n in shape[3:]):
        shape = shape[:3]
    if len(shape) != 3:
        raise MedicalError("4D NIfTI files are not supported; upload a 3D label map.")
    return shape, np.array(img.affine, dtype=np.float64)


def _open_nifti(path: str, workdir: str):
    import nibabel as nib

    try:
        return nib.load(_nifti_path(path, workdir))
    except Exception:
        raise MedicalError(_UNREADABLE)


def _read_nifti_array(img, shape) -> np.ndarray:
    try:
        return np.asanyarray(img.dataobj).reshape(shape)
    except Exception:
        raise MedicalError(_UNREADABLE)


def _load_nifti(path: str, workdir: str) -> Source:
    img = _open_nifti(path, workdir)
    shape, affine = _nifti_geometry(img)
    layers = _array_layers(_read_nifti_array(img, shape))
    return Source(Grid(shape, affine, "RAS"), layers, empty_error=_NO_LABELS)


# ------------------------------------------------------------------- NRRD

_LPS_NAMES = ("left-posterior-superior", "lps")
_RAS_NAMES = ("right-anterior-superior", "ras")


def _nrrd_geometry(header: dict) -> tuple:
    """(spatial_axes, shape, affine, frame, notes) from an NRRD header."""
    sizes = [int(s) for s in header.get("sizes", [])]
    notes = []
    directions = header.get("space directions")
    spatial = None
    if directions is not None:
        directions = np.asarray(directions, dtype=np.float64)
        spatial = [a for a in range(len(sizes)) if np.all(np.isfinite(directions[a]))]
        vectors = [directions[a] for a in spatial]
    elif header.get("spacings") is not None:
        spatial = list(range(len(sizes)))
        vectors = [np.eye(3)[a] * float(s) for a, s in enumerate(header["spacings"][:3])]
    if spatial is None or len(spatial) != 3:
        raise MedicalError("This NRRD file has no usable spatial information (space directions).")
    if any(a >= 1 for a in range(len(sizes)) if a not in spatial):
        raise MedicalError("This NRRD file has an unsupported axis layout.")
    affine = np.eye(4)
    affine[:3, :3] = np.array(vectors).T
    origin = header.get("space origin")
    if origin is not None:
        affine[:3, 3] = np.asarray(origin, dtype=np.float64)[:3]
    space = str(header.get("space", "")).strip().lower()
    if space in _RAS_NAMES:
        frame = "RAS"
    else:
        frame = "LPS"
        if space not in _LPS_NAMES:
            notes.append("No coordinate system was found in the NRRD header; assumed LPS (the 3D Slicer default).")
    return spatial, tuple(sizes[a] for a in spatial), affine, frame, notes


def _parse_color(text) -> str | None:
    try:
        r, g, b = (float(v) for v in str(text).split()[:3])
    except (ValueError, TypeError):
        return None
    return rgb_to_hex((r * 255, g * 255, b * 255))


def _load_nrrd(path: str) -> Source:
    import nrrd

    try:
        data, header = nrrd.read(path)
    except Exception:
        raise MedicalError(_UNREADABLE)
    spatial, shape, affine, frame, notes = _nrrd_geometry(header)
    layer_axis = data.ndim == 4

    segments = sorted({int(m.group(1)) for k in header for m in [re.fullmatch(r"Segment(\d+)_Name", k)] if m})
    if segments:
        layers = []
        for i in segments:
            try:
                value = int(header.get(f"Segment{i}_LabelValue", i + 1))
                layer = int(header.get(f"Segment{i}_Layer", 0))
            except (ValueError, TypeError):
                continue
            if layer_axis:
                if not 0 <= layer < data.shape[0]:
                    continue
                plane = data[layer]
            else:
                plane = data
            name = str(header.get(f"Segment{i}_Name", "")).strip() or f"Segment {i + 1}"
            layers.append(
                LayerSource(name, _parse_color(header.get(f"Segment{i}_Color")), (lambda p=plane, v=value: p == v))
            )
    else:
        if layer_axis:
            raise MedicalError("4D NRRD files are only supported when they come from 3D Slicer (.seg.nrrd).")
        layers = _array_layers(data)
    return Source(Grid(shape, affine, frame), layers, notes=notes, empty_error=_NO_LABELS)


# --------------------------------------------------------------- DICOM-SEG

_D50_TO_D65 = np.array(
    [[0.9555766, -0.0230393, 0.0631636], [-0.0282895, 1.0099416, 0.0210077], [0.0122982, -0.0204830, 1.3299098]]
)
_XYZ_TO_SRGB = np.array(
    [[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560], [0.0556434, -0.2040259, 1.0572252]]
)
_D50_WHITE = np.array([0.96422, 1.0, 0.82521])


def cielab_dicom_to_hex(values) -> str | None:
    """DICOM RecommendedDisplayCIELabValue (16-bit scaled, D50) -> sRGB hex."""
    try:
        l16, a16, b16 = (float(v) for v in values)
    except (TypeError, ValueError):
        return None
    lab_l = l16 / 65535.0 * 100.0
    lab_a = a16 / 65535.0 * 255.0 - 128.0
    lab_b = b16 / 65535.0 * 255.0 - 128.0
    fy = (lab_l + 16.0) / 116.0
    f = np.array([fy + lab_a / 500.0, fy, fy - lab_b / 200.0])
    eps, kappa = 216.0 / 24389.0, 24389.0 / 27.0
    xyz = np.where(f**3 > eps, f**3, (116.0 * f - 16.0) / kappa) * _D50_WHITE
    lin = np.clip(_XYZ_TO_SRGB @ (_D50_TO_D65 @ xyz), 0.0, 1.0)
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return rgb_to_hex(srgb * 255.0)


def _dig(node, *names):
    """Follow ``node.A[0].B[0].C``; None when any step is absent or an empty sequence."""
    from pydicom.sequence import Sequence

    for name in names:
        node = getattr(node, name, None)
        if isinstance(node, Sequence):
            node = node[0] if len(node) else None
        if node is None:
            return None
    return node


def _load_dicom_seg(path: str) -> Source:
    import pydicom

    try:
        ds = pydicom.dcmread(path, force=True)
    except Exception:
        raise MedicalError(_UNREADABLE)
    if str(getattr(ds, "Modality", "")) != "SEG":
        raise MedicalError("This DICOM file is not a segmentation.")
    try:
        n_frames = int(getattr(ds, "NumberOfFrames", 1) or 1)
        rows, cols = int(ds.Rows), int(ds.Columns)
    except (AttributeError, TypeError, ValueError):
        raise MedicalError(_UNREADABLE)
    if n_frames * rows * cols > 8 * max_voxels():
        raise MedicalError("This segmentation is too large to process. Try a smaller series or crop it.")
    try:
        pixels = np.asarray(ds.pixel_array).reshape(n_frames, rows, cols)
    except Exception:
        raise MedicalError("This DICOM segmentation could not be decoded (try an uncompressed export).")
    if str(getattr(ds, "SegmentationType", "BINARY")).upper() == "FRACTIONAL":
        pixels = pixels > 0.5 * float(getattr(ds, "MaximumFractionalValue", 255) or 255)
    else:
        pixels = pixels > 0

    per_frame = getattr(ds, "PerFrameFunctionalGroupsSequence", None)
    if per_frame is None or len(per_frame) != n_frames:
        raise MedicalError("This DICOM segmentation has no per-frame position information.")
    shared = _dig(ds, "SharedFunctionalGroupsSequence")
    iop = _dig(shared, "PlaneOrientationSequence", "ImageOrientationPatient") if shared else None
    if iop is None:
        iop = _dig(per_frame[0], "PlaneOrientationSequence", "ImageOrientationPatient")
    measures = _dig(shared, "PixelMeasuresSequence") if shared else None
    if measures is None:
        measures = _dig(per_frame[0], "PixelMeasuresSequence")
    spacing = getattr(measures, "PixelSpacing", None) if measures is not None else None
    if iop is None or spacing is None:
        raise MedicalError("This DICOM segmentation is missing orientation or pixel spacing.")
    iop = np.array([float(v) for v in iop])
    spacing = [float(v) for v in spacing]
    row_dir, col_dir = iop[:3], iop[3:]
    normal = np.cross(row_dir, col_dir)
    normal /= np.linalg.norm(normal) or 1.0

    seg_numbers, positions = [], []
    for frame in per_frame:
        ref = _dig(frame, "SegmentIdentificationSequence", "ReferencedSegmentNumber")
        ipp = _dig(frame, "PlanePositionSequence", "ImagePositionPatient")
        if ref is None or ipp is None:
            raise MedicalError("This DICOM segmentation has frames without segment or position information.")
        seg_numbers.append(int(ref))
        positions.append([float(v) for v in ipp])
    positions = np.array(positions)
    proj = positions @ normal

    # Slice index of every frame. Frames of empty slices may be omitted, so the
    # step is the smallest distinct gap rather than the mean.
    order = np.sort(proj)
    gaps = np.diff(order)
    gaps = gaps[gaps > 1e-3]
    dz = float(gaps.min()) if gaps.size else None
    if dz is None:
        dz = float(getattr(measures, "SpacingBetweenSlices", None) or getattr(measures, "SliceThickness", None) or 1.0)
    base = float(order[0])
    slice_idx = np.rint((proj - base) / dz).astype(int)
    if np.max(np.abs(proj - (base + slice_idx * dz))) > 0.1 * dz:
        raise MedicalError("The slice spacing of this DICOM segmentation is irregular.")
    n_slices = int(slice_idx.max()) + 1
    if cols * rows * n_slices > 8 * max_voxels():
        raise MedicalError("This segmentation is too large to process. Try a smaller series or crop it.")
    first = int(np.argmin(proj))

    affine = np.eye(4)
    affine[:3, 0] = row_dir * spacing[1]
    affine[:3, 1] = col_dir * spacing[0]
    affine[:3, 2] = normal * dz
    affine[:3, 3] = positions[first]

    layers = []
    for seg in getattr(ds, "SegmentSequence", []):
        number = int(seg.SegmentNumber)
        frame_ids = [f for f, s in enumerate(seg_numbers) if s == number]
        if not frame_ids:
            continue
        name = str(getattr(seg, "SegmentLabel", "") or "").strip() or f"Segment {number}"
        color = cielab_dicom_to_hex(getattr(seg, "RecommendedDisplayCIELabValue", None))

        def load(frame_ids=frame_ids):
            vol = np.zeros((cols, rows, n_slices), dtype=bool)
            for f in frame_ids:
                vol[:, :, slice_idx[f]] |= pixels[f].T
            return vol

        layers.append(LayerSource(name, color, load, count=int(pixels[frame_ids].sum())))
    return Source(Grid((cols, rows, n_slices), affine, "LPS"), layers, empty_error=_NO_LABELS)


# ------------------------------------------------------------- ZIP of masks

def _mask_name(filename: str) -> str:
    stem = os.path.basename(filename)
    lowered = stem.lower()
    for suffix in (".nii.gz", ".nii", ".seg.nrrd", ".nrrd"):
        if lowered.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    stem = stem.replace("_", " ").strip()
    return stem[:1].upper() + stem[1:] if stem else "Mask"


def _natural_key(path: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path)]


def _mask_reader(path: str, workdir: str):
    """(shape, affine, frame, load) for one binary mask file; load() returns a bool array."""
    lowered = path.lower()
    if lowered.endswith(".nrrd"):
        import nrrd

        try:
            header = nrrd.read_header(path)
        except Exception:
            raise MedicalError(_UNREADABLE)
        if int(header.get("dimension", 0)) != 3:
            raise MedicalError("Each mask file must be a single 3D volume.")
        _spatial, shape, affine, frame, _notes = _nrrd_geometry(header)

        def load():
            try:
                data, _h = nrrd.read(path)
            except Exception:
                raise MedicalError(_UNREADABLE)
            return _to_mask(data)

        return shape, affine, frame, load
    img = _open_nifti(path, workdir)
    shape, affine = _nifti_geometry(img)
    return shape, affine, "RAS", lambda: _to_mask(_read_nifti_array(img, shape))


def _to_mask(data: np.ndarray) -> np.ndarray:
    if np.issubdtype(data.dtype, np.floating):
        return data > 0.5
    return data != 0


def _load_mask_zip(zip_path: str, workdir: str) -> Source:
    extracted = [p for p in safe_extract(zip_path, workdir) if is_segmentation_name(p)]
    if not extracted:
        raise MedicalError("This ZIP contains no .nii, .nii.gz or .nrrd mask files.")
    extracted.sort(key=_natural_key)

    grid = None
    layers = []
    for path in extracted:
        shape, affine, frame, load = _mask_reader(path, workdir)
        if grid is None:
            grid = Grid(shape, affine, frame)
        elif shape != grid.shape or frame != grid.frame or not np.allclose(affine, grid.affine, atol=1e-3):
            raise MedicalError("The mask files in this ZIP do not share the same size and orientation.")
        layers.append(LayerSource(_mask_name(path), None, load))
    return Source(grid, layers, empty_error=_NO_LABELS)
