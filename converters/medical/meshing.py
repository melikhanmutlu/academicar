"""Label masks -> smoothed surface meshes -> one layered GLB.

Every reader hands over a ``Source``: a ``Grid`` (shape + voxel-index ->
patient-mm affine + the patient axis convention) and a list of ``LayerSource``
objects. This module owns striding, marching cubes, the patient -> glTF axis
change and the export.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from . import progress
from .common import LAYER_PALETTE, MAX_LAYERS, MedicalError, max_faces, max_voxels

# glTF is Y-up. DICOM/NRRD LPS (x=L, y=P, z=S) -> (X=L, Y=S, Z=A) = (x, z, -y);
# NIfTI RAS (x=R, y=A, z=S) -> (X=-R, Y=S, Z=A) = (-x, z, y). Both are proper
# rotations (determinant +1), so nothing gets mirrored.
_FRAME_TO_GLTF = {
    "LPS": np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]]),
    "RAS": np.array([[-1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]]),
}

_ROUGHNESS = 0.55
_MAX_REDUCTIONS = 3


@dataclass
class Grid:
    shape: tuple
    affine: np.ndarray  # 4x4, voxel index -> patient mm in ``frame``
    frame: str  # "LPS" or "RAS"


@dataclass
class LayerSource:
    name: str
    color: Optional[str]
    load: Callable[[], np.ndarray]  # full-resolution boolean mask on the Grid
    count: Optional[int] = None  # voxel count when known cheaply (for the layer cap)
    # True for built-in preset labels (Bone, Skin, ...), which may be shown in progress text;
    # a segmentation's own structure names are not.
    public_name: bool = False


@dataclass
class Source:
    grid: Grid
    layers: list
    notes: list = field(default_factory=list)
    empty_error: str = "No structures were found in this segmentation."
    modality: Optional[str] = None


def voxel_spacing(affine: np.ndarray) -> np.ndarray:
    return np.linalg.norm(affine[:3, :3], axis=0)


def _strided_count(shape, strides) -> int:
    return math.prod(math.ceil(n / s) for n, s in zip(shape, strides))


def choose_strides(shape, spacing, cap: int) -> tuple:
    """Smallest integer strides so the strided grid has at most ``cap`` voxels.

    The axis with the finest effective spacing is coarsened first, so in-plane
    axes of a CT go before the (usually thicker) slice axis and voxels stay
    close to isotropic.
    """
    strides = [1, 1, 1]
    while _strided_count(shape, strides) > cap:
        candidates = [a for a in range(3) if strides[a] < shape[a]]
        if not candidates:
            break
        axis = min(candidates, key=lambda a: spacing[a] * strides[a])
        strides[axis] += 1
    return tuple(strides)


def hex_to_rgb(color: str) -> tuple:
    color = color.lstrip("#")
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(round(float(c))))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def srgb_to_linear(c: float) -> float:
    # Same conversion as stl_converter._srgb_to_linear (glTF baseColorFactor is linear).
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _mesh_mask(mask: np.ndarray, affine: np.ndarray):
    """Smooth one boolean mask and return (vertices_mm, faces) in patient space."""
    from scipy import ndimage
    from skimage import measure

    # Work on the bounding box only (plus room for the smoothing kernel); the
    # full volume can be tens of millions of voxels.
    boxes = ndimage.find_objects(mask.astype(np.uint8))
    if not boxes:
        return None
    margin = 4
    lo = [max(0, b.start - margin) for b in boxes[0]]
    hi = [min(n, b.stop + margin) for b, n in zip(boxes[0], mask.shape)]
    crop = mask[tuple(slice(a, b) for a, b in zip(lo, hi))]
    padded = np.pad(crop.astype(np.float32), 1)  # 1 voxel of air so surfaces close
    # Thin structures (vessels, nerves 1-2 voxels wide) never reach 0.5 under the full
    # smoothing, so retry with weaker smoothing and finally on the raw binary mask.
    for sigma in (1.0, 0.5, None):
        field = padded if sigma is None else ndimage.gaussian_filter(padded, sigma=sigma)
        if field.max() < 0.5:
            continue
        try:
            verts, faces, _normals, _values = measure.marching_cubes(field, level=0.5)
        except (ValueError, RuntimeError):
            continue
        break
    else:
        return None
    # Index space -> patient mm through the full affine (direction cosines +
    # origin), so oblique acquisitions land where they were scanned.
    idx = verts + (np.array(lo, dtype=np.float64) - 1.0)
    world = idx @ affine[:3, :3].T + affine[:3, 3]
    return world, faces.astype(np.int64)


def _unique_names(names):
    used: set[str] = set()
    out = []
    for name in names:
        base = (name or "Layer").strip() or "Layer"
        candidate, n = base, 1
        while candidate.lower() in used:
            n += 1
            candidate = f"{base} ({n})"
        used.add(candidate.lower())
        out.append(candidate)
    return out


def _mesh_layers(source: Source, strides: tuple, layers: list):
    """Mesh the given layers at ``strides``. Returns ([(layer, mesh, ml)], faces, notes)."""
    import trimesh

    affine = source.grid.affine.copy()
    affine[:3, :3] = affine[:3, :3] * np.array(strides, dtype=np.float64)  # scales the columns
    voxel_mm3 = abs(float(np.linalg.det(affine[:3, :3])))
    rot = _FRAME_TO_GLTF[source.grid.frame]
    sl = tuple(slice(None, None, s) for s in strides)

    built = []
    notes = []
    total_faces = 0
    for index, layer in enumerate(layers):
        # Surfaces take most of the time: 55-92% of the child's work, split per structure.
        start = 55 + 37 * index / len(layers)
        label = f"{layer.name} surface" if layer.public_name else "structure surface"
        progress.report(start, f"Extracting {label} ({index + 1} / {len(layers)})", force=True)
        full = np.asarray(layer.load())
        mask = full[sl]
        voxels = int(mask.sum())
        if voxels == 0:
            if full.any():  # present at full resolution but stepped over by the stride
                notes.append(f"{layer.name} is too thin to display at this resolution.")
            continue
        progress.report(start + 18.5 / len(layers), "Smoothing and simplifying", force=True)
        result = _mesh_mask(mask, affine)
        del mask
        if result is None:
            notes.append(f"{layer.name} is too thin to display at this resolution.")
            continue
        world, faces = result
        verts = (world @ rot.T) / 1000.0  # patient mm -> glTF axes, metres
        mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        if mesh.volume < 0:
            mesh.invert()  # keep normals pointing outward
        total_faces += len(mesh.faces)
        built.append((layer, mesh, voxels * voxel_mm3 / 1000.0))
    return built, total_faces, notes


def fmt_mm(voxel_mm) -> str:
    return "x".join(f"{v:.2f}".rstrip("0").rstrip(".") for v in voxel_mm) + " mm"


def build_glb(source: Source, output_path: str):
    """Mesh every layer of ``source`` and write the layered GLB.

    Returns ``(layers, notes, voxel_mm)`` where layers is
    ``[{"name", "color", "volume_ml"}, ...]``.
    """
    notes = list(source.notes)
    grid = source.grid
    spacing = voxel_spacing(grid.affine)
    layers = list(source.layers)

    strides = choose_strides(grid.shape, spacing, max_voxels())
    resample_note = None
    if strides != (1, 1, 1):
        resample_note = f"Downsampled to {fmt_mm(spacing * np.array(strides))} voxels to fit processing limits."

    if len(layers) > MAX_LAYERS:
        sl = tuple(slice(None, None, s) for s in strides)
        for layer in layers:
            if layer.count is None:
                layer.count = int(np.asarray(layer.load())[sl].sum())
            else:
                layer.count = int(layer.count / math.prod(strides))
        layers = sorted(layers, key=lambda l: -(l.count or 0))[:MAX_LAYERS]
        notes.append(f"Only the {MAX_LAYERS} largest structures are shown; the rest were left out.")

    cap = max_faces()
    built, faces, mesh_notes = _mesh_layers(source, strides, layers)
    attempts = 0
    while faces > cap and attempts < _MAX_REDUCTIONS:
        attempts += 1
        # Faces scale roughly with stride^-2 and voxels with stride^-3.
        shrink = max(1.5, (faces / cap) ** 1.5 * 1.1)
        target = max(1000, int(_strided_count(grid.shape, strides) / shrink))
        strides = choose_strides(grid.shape, spacing, target)
        resample_note = (
            f"Downsampled to {fmt_mm(spacing * np.array(strides))} voxels to keep the 3D model a manageable size."
        )
        built, faces, mesh_notes = _mesh_layers(source, strides, layers)
    if faces > cap:
        raise MedicalError("This scan produces a 3D model that is too complex. Try a smaller series or crop it.")
    if not built:
        raise MedicalError(source.empty_error)
    if resample_note:
        notes.append(resample_note)
    notes.extend(mesh_notes)

    names = _unique_names([b[0].name for b in built])
    colors = [
        (layer.color or LAYER_PALETTE[i % len(LAYER_PALETTE)]).upper()
        for i, (layer, _mesh, _ml) in enumerate(built)
    ]

    # Centre the whole model (one shared offset, so layers stay registered).
    all_verts = np.concatenate([m.vertices for _l, m, _v in built])
    centre = (all_verts.min(axis=0) + all_verts.max(axis=0)) / 2.0
    for _l, mesh, _v in built:
        mesh.apply_translation(-centre)

    progress.report(93, "Writing model", force=True)
    _export(built, names, colors, output_path)
    progress.report(100, "Writing model", force=True)
    info = [
        {"name": n, "color": c, "volume_ml": round(float(ml), 2)}
        for n, c, (_l, _m, ml) in zip(names, colors, built)
    ]
    return info, notes, [float(x) for x in spacing * np.array(strides)]


def _export(built, names, colors, output_path: str) -> None:
    import pygltflib
    import trimesh
    from trimesh.visual.material import PBRMaterial

    scene = trimesh.Scene()
    for (_layer, mesh, _ml), name, color in zip(built, names, colors):
        r, g, b = hex_to_rgb(color)
        mesh.visual = trimesh.visual.TextureVisuals(
            material=PBRMaterial(
                name=name,
                baseColorFactor=[r, g, b, 255],
                metallicFactor=0.0,
                roughnessFactor=_ROUGHNESS,
                doubleSided=False,
            )
        )
        scene.add_geometry(mesh, node_name=name, geom_name=name)
    glb = scene.export(file_type="glb")

    # trimesh writes the 8-bit colour as sRGB/255, but glTF baseColorFactor is
    # LINEAR (see stl_converter._srgb_to_linear), so rewrite it from the hex.
    gltf = pygltflib.GLTF2.load_from_bytes(glb)
    by_name = dict(zip(names, colors))
    for mat in gltf.materials:
        hex_color = by_name.get(mat.name)
        if hex_color:
            lin = [srgb_to_linear(v / 255.0) for v in hex_to_rgb(hex_color)]
            mat.pbrMetallicRoughness.baseColorFactor = lin + [1.0]
    with open(output_path, "wb") as fh:
        fh.write(b"".join(gltf.save_to_bytes()))
