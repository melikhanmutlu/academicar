"""Per-layer measurements for layered GLBs: structure dimensions and the
distances between nearby structures.

Runs in the worker only, on the *uncompressed* GLB (trimesh cannot read Draco
geometry), so ``process_model_upload_job`` calls it before Draco compression
and the backfill job decompresses the stored GLB first. The GLB is in metres;
reported sizes are millimetres. ``compute_layer_metrics`` never raises.

Result shape::

    {"version": 1, "unit": "mm",
     "layers": {name: {"dims_mm": [a, b, c],        # oriented box edges, desc
                       "max_diameter_mm": d,         # farthest vertex pair
                       "centroid": [x, y, z]}},      # metres
     "pairs": [{"a": name, "b": name, "min_distance_mm": d,
                "point_a": [x, y, z], "point_b": [x, y, z]}],   # metres
     "truncated": True}                              # only when work was cut short
"""

from __future__ import annotations

import logging
import time

import numpy as np
import trimesh
from scipy.spatial import ConvexHull, cKDTree

logger = logging.getLogger(__name__)

METRIC_PAIR_RADIUS_MM = 50.0
METRIC_MAX_LAYERS = 64
METRIC_MAX_PAIRS = 300
METRIC_TIME_BUDGET_S = 20.0
METRIC_SAMPLES_PER_LAYER = 20000
_MAX_HULL_INPUT = 500_000
_MAX_DIAMETER_POINTS = 6000
_SEED = 12345


def _round_points(points) -> list[float]:
    return [round(float(v), 5) for v in points]


def _layer_geometry(scene: trimesh.Scene, layers: list[dict]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Layer name -> (vertices, faces) in scene space (instance transforms applied)."""
    owner: dict[str, str] = {}
    for layer in layers[:METRIC_MAX_LAYERS]:
        name = layer.get("name")
        for material in layer.get("materials") or []:
            owner.setdefault(material, name)

    parts: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    for node in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node]
        geometry = scene.geometry.get(geometry_name)
        if geometry is None or not hasattr(geometry, "faces") or len(geometry.faces) == 0:
            continue
        material = getattr(getattr(geometry, "visual", None), "material", None)
        name = owner.get(getattr(material, "name", None))
        if name is None:
            continue
        vertices = trimesh.transform_points(np.asarray(geometry.vertices, dtype=np.float64), transform)
        parts.setdefault(name, []).append((vertices, np.asarray(geometry.faces)))

    merged = {}
    for name, items in parts.items():
        offset, vertices, faces = 0, [], []
        for v, f in items:
            vertices.append(v)
            faces.append(f + offset)
            offset += len(v)
        merged[name] = (np.vstack(vertices), np.vstack(faces))
    return merged


def _hull_points(vertices: np.ndarray) -> np.ndarray:
    points = np.unique(vertices, axis=0) if len(vertices) <= _MAX_HULL_INPUT else vertices[:: len(vertices) // _MAX_HULL_INPUT + 1]
    try:
        return points[ConvexHull(points).vertices]
    except Exception:  # degenerate (flat/linear) layers: measure the raw points
        return points


def _oriented_extents(points: np.ndarray) -> np.ndarray:
    """Edges of the smaller of the PCA-oriented and the axis-aligned bounding box."""
    aligned = points.max(axis=0) - points.min(axis=0)
    best = aligned
    if len(points) >= 3:
        centred = points - points.mean(axis=0)
        _, vectors = np.linalg.eigh(centred.T @ centred)
        rotated = centred @ vectors
        pca = rotated.max(axis=0) - rotated.min(axis=0)
        if np.prod(pca) < np.prod(aligned):
            best = pca
    return np.sort(best)[::-1]


def _max_diameter(points: np.ndarray) -> float:
    if len(points) > _MAX_DIAMETER_POINTS:
        points = points[:: len(points) // _MAX_DIAMETER_POINTS + 1]
    best = 0.0
    for start in range(0, len(points), 500):
        chunk = points[start:start + 500]
        distances = np.linalg.norm(chunk[:, None, :] - points[None, :, :], axis=2)
        best = max(best, float(distances.max()))
    return best


def _surface_points(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Deterministic points on the layer's surface (plus its vertices when few)."""
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    try:
        samples, _ = trimesh.sample.sample_surface(mesh, METRIC_SAMPLES_PER_LAYER, seed=_SEED)
    except Exception:
        samples = vertices[:: len(vertices) // METRIC_SAMPLES_PER_LAYER + 1]
    if len(vertices) <= 5000:
        samples = np.vstack([samples, vertices])
    return np.asarray(samples, dtype=np.float64)


def _box_gap(lo_a, hi_a, lo_b, hi_b) -> float:
    gap = np.maximum(0.0, np.maximum(lo_a - hi_b, lo_b - hi_a))
    return float(np.linalg.norm(gap))


def _box_distance(points: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Distance from each point to the axis-aligned box [lo, hi] (0 inside)."""
    return np.linalg.norm(np.maximum(0.0, np.maximum(lo - points, points - hi)), axis=1)


def _nearest_pair(points_a: np.ndarray, points_b: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Closest pair between two point clouds: (distance, point in a, point in b).

    A coarse query gives an upper bound; only points that could beat it (those
    within the bound of the other cloud's box) go into the exact k-d tree query,
    which is slow for far apart, dense shells.
    """
    coarse_a = points_a[:: len(points_a) // 1000 + 1]
    distances, _ = cKDTree(points_b).query(coarse_a)
    bound = float(distances.min()) * 1.0001 + 1e-9
    near_a = points_a[_box_distance(points_a, points_b.min(axis=0), points_b.max(axis=0)) <= bound]
    near_b = points_b[_box_distance(points_b, points_a.min(axis=0), points_a.max(axis=0)) <= bound]
    if len(near_a) <= len(near_b):
        dist, idx = cKDTree(near_b).query(near_a)
        i = int(np.argmin(dist))
        return float(dist[i]), near_a[i], near_b[idx[i]]
    dist, idx = cKDTree(near_a).query(near_b)
    i = int(np.argmin(dist))
    return float(dist[i]), near_a[idx[i]], near_b[i]


def compute_layer_metrics(glb_path: str, layers: list[dict]) -> dict | None:
    """Dimensions per layer and min distances between nearby layers, or None."""
    try:
        started = time.monotonic()
        scene = trimesh.load(glb_path, force="scene")
        geometry = _layer_geometry(scene, layers)
        if not geometry:
            return None

        truncated = len(layers) > METRIC_MAX_LAYERS
        order = [layer["name"] for layer in layers[:METRIC_MAX_LAYERS] if layer.get("name") in geometry]
        result_layers, boxes = {}, {}
        for name in order:
            vertices, _ = geometry[name]
            hull = _hull_points(vertices)
            boxes[name] = (vertices.min(axis=0), vertices.max(axis=0))
            result_layers[name] = {
                "dims_mm": [round(float(v) * 1000.0, 1) for v in _oriented_extents(hull)],
                "max_diameter_mm": round(_max_diameter(hull) * 1000.0, 1),
                "centroid": _round_points(vertices.mean(axis=0)),
            }

        radius_m = METRIC_PAIR_RADIUS_MM / 1000.0
        candidates = []
        for i, a in enumerate(order):
            for b in order[i + 1:]:
                gap = _box_gap(*boxes[a], *boxes[b])
                if gap <= radius_m:
                    candidates.append((gap, a, b))
        candidates.sort(key=lambda item: item[0])  # closest first, so any cut keeps the most relevant

        samples: dict[str, np.ndarray] = {}

        def sampled(name: str) -> np.ndarray:
            if name not in samples:
                samples[name] = _surface_points(*geometry[name])
            return samples[name]

        pairs = []
        for _, a, b in candidates:
            if len(pairs) >= METRIC_MAX_PAIRS or time.monotonic() - started > METRIC_TIME_BUDGET_S:
                truncated = True
                break
            distance, point_a, point_b = _nearest_pair(sampled(a), sampled(b))
            pairs.append({
                "a": a,
                "b": b,
                "min_distance_mm": round(distance * 1000.0, 1),
                "point_a": _round_points(point_a),
                "point_b": _round_points(point_b),
            })

        metrics = {"version": 1, "unit": "mm", "layers": result_layers, "pairs": pairs}
        if truncated:
            metrics["truncated"] = True
        return metrics
    except Exception as exc:
        # Type only: the message can embed the file path, which may carry patient
        # information. Callers log the model id.
        logger.warning("Layer measurements failed (%s)", type(exc).__name__)
        return None


def restrict_metrics(metrics: dict | None, layer_names) -> dict | None:
    """``metrics`` limited to the named layers (None when nothing is left)."""
    if not isinstance(metrics, dict):
        return None
    keep = set(layer_names)
    kept = {name: value for name, value in (metrics.get("layers") or {}).items() if name in keep}
    if not kept:
        return None
    pairs = [p for p in metrics.get("pairs") or [] if p.get("a") in kept and p.get("b") in kept]
    return {**metrics, "layers": kept, "pairs": pairs}


def rename_metrics_layers(metrics: dict | None, mapping: dict[str, str]) -> dict | None:
    """``metrics`` with layers renamed per ``{old: new}`` (applied at once, so swaps work)."""
    if not isinstance(metrics, dict):
        return metrics
    return {
        **metrics,
        "layers": {mapping.get(name, name): value for name, value in (metrics.get("layers") or {}).items()},
        "pairs": [
            {**p, "a": mapping.get(p.get("a"), p.get("a")), "b": mapping.get(p.get("b"), p.get("b"))}
            for p in metrics.get("pairs") or []
        ],
    }
