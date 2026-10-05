"""Existing segmentations (NIfTI, NRRD / Slicer .seg.nrrd, DICOM-SEG, ZIP of masks) -> layers.

Each non-empty label becomes one ``LayerSource``. Masks are loaded lazily, one at
a time, so a TotalSegmentator folder with ~100 structures never sits in memory
at once.
"""

from __future__ import annotations

import json
import os
import re

import numpy as np

from .common import MedicalError, is_segmentation_name, max_voxels, safe_extract
from .meshing import Grid, LayerSource, Source, rgb_to_hex

_NIFTI_SUFFIXES = (".nii.gz", ".nii")
_NOT_SEGMENTATION = "This file does not look like a segmentation (it contains continuous image intensities)."
_ONLY_IMAGES = "This ZIP contains no mask files (only images)."
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


# More distinct values than this means an image, not a label map (TotalSegmentator has ~120 classes).
_MAX_LABELS = 255


def _classify(arr: np.ndarray):
    """What a volume's values say it is: ("mask" | "labels" | "image", {label: voxels} or None).

    Binary data (0/1, bool, floats in [0, 1]) and volumes with a single non-zero
    value are masks; integer maps with two or more non-zero labels are label
    maps; continuous intensities (non-integer floats, negative values such as
    HU, hundreds of distinct values) are images.
    """
    if arr.dtype == np.bool_:
        arr = arr.astype(np.uint8)
    if arr.size == 0:
        return "image", None
    if np.issubdtype(arr.dtype, np.floating):
        if not np.isfinite(arr).all():
            return "image", None
        if arr.min() >= 0 and arr.max() <= 1.0 + 1e-6:
            return "mask", None  # binary float mask (e.g. resampled or probability-style)
        if not np.array_equal(arr, np.rint(arr)):
            return "image", None
        arr = arr.astype(np.int32)
    elif not np.issubdtype(arr.dtype, np.integer):
        return "image", None
    if arr.min() < 0:
        return "image", None
    counts = _label_counts(arr)
    if len(counts) > _MAX_LABELS:
        return "image", None
    return ("labels" if len(counts) >= 2 else "mask"), counts


def _array_layers(arr: np.ndarray, table: dict | None = None) -> list[LayerSource]:
    """Layers for a 3D label map (or binary mask), named "Label N" unless ``table`` names N."""
    kind, counts = _classify(arr)
    if kind == "image":
        raise MedicalError(_NOT_SEGMENTATION)
    if counts is None:
        return [LayerSource("Label 1", None, lambda: arr > 0.5)]
    layers = []
    for v, c in sorted(counts.items()):
        name, color = (table or {}).get(v, (None, None))
        layers.append(LayerSource(name or f"Label {v}", color, (lambda v=v: arr == v), count=c))
    return layers


# --------------------------------------------------------------- label tables
#
# Optional names (and colours) for label values: an ITK-SNAP label description
# file, a 3D Slicer colour table or a JSON object. A table is a dict
# {value: (name or None, "#RRGGBB" or None)}; anything unparseable is ignored.

_TABLE_SUFFIXES = (".txt", ".ctbl", ".json", ".lut")
_TABLE_MAX_BYTES = 1 << 20
_ITK_SNAP_LINE = re.compile(r'(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+[\d.]+\s+\d+\s+\d+\s+"(.*)"')
_SLICER_LINE = re.compile(r"(\d+)\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+\d+")


def _rgb_hex(r, g, b) -> str | None:
    try:
        return rgb_to_hex((int(r), int(g), int(b)))
    except (ValueError, TypeError):
        return None


def _table_from_text(text: str) -> dict:
    itk, slicer = {}, {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _ITK_SNAP_LINE.fullmatch(line)
        if m:
            itk[int(m.group(1))] = (m.group(5).strip() or None, _rgb_hex(*m.group(2, 3, 4)))
            continue
        m = _SLICER_LINE.fullmatch(line)
        if m and not m.group(2).isdigit():  # a name, not just more numbers
            slicer[int(m.group(1))] = (m.group(2).strip(), _rgb_hex(*m.group(3, 4, 5)))
    return itk or slicer


def _json_color(value) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"#?[0-9a-fA-F]{6}", value.strip()):
        return "#" + value.strip().lstrip("#").upper()
    if isinstance(value, list) and len(value) == 3:
        return _rgb_hex(*value)
    return None


def _table_from_json(data) -> dict:
    """{"1": "liver"} or {"1": {"name": "liver", "color": "#aa0000"}}; every key must be an integer."""
    if not isinstance(data, dict) or not data:
        return {}
    table = {}
    for key, value in data.items():
        try:
            number = int(key)
        except (ValueError, TypeError):
            return {}
        if isinstance(value, str):
            table[number] = (value.strip() or None, None)
        elif isinstance(value, dict) and isinstance(value.get("name"), str):
            table[number] = (value["name"].strip() or None, _json_color(value.get("color")))
        else:
            return {}
    return table


def _table_from_bytes(raw: bytes) -> dict:
    try:
        text = raw.rstrip(b"\x00").decode("utf-8", errors="replace")
        try:
            return _table_from_json(json.loads(text))
        except ValueError:
            return _table_from_text(text)
    except Exception:
        return {}


def _read_label_tables(paths) -> dict:
    table: dict = {}
    for path in sorted(paths, key=_natural_key):
        try:
            with open(path, "rb") as fh:
                raw = fh.read(_TABLE_MAX_BYTES)
        except OSError:
            continue
        table.update(_table_from_bytes(raw))
    table.pop(0, None)
    return table


def _nifti_header_labels(img) -> dict:
    """Label names some tools store as JSON ({"1": "liver"}) in a NIfTI header extension."""
    try:
        for ext in img.header.extensions:
            content = ext.get_content()
            if isinstance(content, str):
                content = content.encode("utf-8", errors="replace")
            data = json.loads(bytes(content).rstrip(b"\x00").decode("utf-8"))
            if isinstance(data, dict) and all(isinstance(v, str) for v in data.values()):
                table = _table_from_json(data)
                if table:
                    return table
    except Exception:
        pass  # not a label table (or not JSON at all): ignore it
    return {}


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


def _load_nifti(path: str, workdir: str, table: dict | None = None) -> Source:
    img = _open_nifti(path, workdir)
    shape, affine = _nifti_geometry(img)
    layers = _array_layers(_read_nifti_array(img, shape), {**_nifti_header_labels(img), **(table or {})})
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


def _load_nrrd(path: str, table: dict | None = None) -> Source:
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
        layers = _array_layers(data, table)
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
    """(shape, affine, frame, read, names) for one mask file; read() returns the raw array."""
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

        def read():
            try:
                data, _h = nrrd.read(path)
            except Exception:
                raise MedicalError(_UNREADABLE)
            return data

        return shape, affine, frame, read, {}
    img = _open_nifti(path, workdir)
    shape, affine = _nifti_geometry(img)
    return shape, affine, "RAS", lambda: _read_nifti_array(img, shape), _nifti_header_labels(img)


def _to_mask(data: np.ndarray) -> np.ndarray:
    if np.issubdtype(data.dtype, np.floating):
        return data > 0.5
    return data != 0


class _LastVolume:
    """Keeps only the most recently read volume, so a ZIP of masks never sits in memory at once."""

    def __init__(self):
        self.key = self.data = None

    def get(self, key, read):
        if self.key != key:
            self.key = self.data = None  # release the previous volume before reading the next
            self.data = read()
            self.key = key
        return self.data


def _load_single_mask_file(path: str, workdir: str, table: dict) -> Source:
    """A ZIP with one segmentation file is that file uploaded directly (same layer names)."""
    try:
        if path.lower().endswith(".nrrd"):
            return _load_nrrd(path, table)
        return _load_nifti(path, workdir, table)
    except MedicalError as exc:
        if str(exc) == _NOT_SEGMENTATION:
            raise MedicalError(_ONLY_IMAGES)
        raise


def _load_mask_zip(zip_path: str, workdir: str) -> Source:
    extracted = safe_extract(zip_path, workdir)
    files = sorted((p for p in extracted if is_segmentation_name(p)), key=_natural_key)
    if not files:
        raise MedicalError("This ZIP contains no .nii, .nii.gz or .nrrd mask files.")
    table = _read_label_tables([p for p in extracted if p.lower().endswith(_TABLE_SUFFIXES)])
    if len(files) == 1:
        return _load_single_mask_file(files[0], workdir, table)

    grid = None
    layers, notes = [], []
    volumes = _LastVolume()
    for path in files:
        shape, affine, frame, read, header_names = _mask_reader(path, workdir)
        kind, counts = _classify(volumes.get(path, read))
        if kind == "image":  # e.g. the CT zipped next to its masks
            notes.append(f"Skipped {os.path.basename(path)[:60]}: it looks like an image, not a mask.")
            continue
        if grid is None:
            grid = Grid(shape, affine, frame)
        elif shape != grid.shape or frame != grid.frame or not np.allclose(affine, grid.affine, atol=1e-3):
            raise MedicalError("The mask files in this ZIP do not share the same size and orientation.")
        stem = _mask_name(path)
        if kind == "mask":
            layers.append(LayerSource(stem, None, lambda p=path, r=read: _to_mask(volumes.get(p, r))))
            continue
        names = {**header_names, **table}
        for value, count in sorted(counts.items()):
            name, color = names.get(value, (None, None))
            layers.append(
                LayerSource(name or f"{stem} {value}", color, (lambda p=path, r=read, v=value: volumes.get(p, r) == v), count=count)
            )
    if grid is None:
        raise MedicalError(_ONLY_IMAGES)
    return Source(grid, layers, notes=notes, empty_error=_NO_LABELS)
