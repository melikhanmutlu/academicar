"""Best-effort poster/thumbnail generation from GLB files.

Render pipeline (first success wins):
1. pyrender EGL offscreen (full PBR, requires GPU/Mesa)
2. trimesh scene.save_image (pyglet software, simpler shading)
3. Rasterized orthographic projection via Pillow (no GL dependency)

If all fail the function returns False and the viewer works without a poster.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import numpy as np

from .glb_optimize import decompress_glb, glb_has_draco

# Must be set before pyrender/PyOpenGL is imported anywhere in the process —
# forces the OSMesa software backend instead of EGL (no GPU) or a windowed
# GLUT/pyglet context (no display) on headless deployment hosts.
os.environ.setdefault("PYOPENGL_PLATFORM", "osmesa")

logger = logging.getLogger(__name__)

POSTER_SIZE = (1024, 1024)
BG_COLOR = (245, 245, 245, 255)
MESH_COLOR = (160, 170, 180, 255)


def generate_poster(glb_path: str, png_path: str) -> bool:
    """Generate a poster image from a GLB file. Returns True on success.

    All three render backends load geometry via trimesh, which cannot decode
    Draco-compressed meshes (see finalize_converted_glb / optimize_glb) — the
    same limitation the Blender USDZ export path already works around. When
    the stored GLB is Draco-compressed, render from a decompressed scratch
    copy instead so poster generation doesn't silently fail for every
    optimized model.
    """
    if not os.path.isfile(glb_path):
        return False
    render_path = glb_path
    tmp_decompressed = None
    if glb_has_draco(glb_path):
        tmp_decompressed = f"{glb_path}.posterdecomp.{os.getpid()}.{threading.get_ident()}.glb"
        if decompress_glb(glb_path, tmp_decompressed):
            render_path = tmp_decompressed
        else:
            tmp_decompressed = None
    try:
        try:
            if _try_pyrender(render_path, png_path):
                return True
        except Exception:
            logger.debug("pyrender poster failed", exc_info=True)
        try:
            if _try_trimesh_scene(render_path, png_path):
                return True
        except Exception:
            logger.debug("trimesh scene poster failed", exc_info=True)
        try:
            if _try_rasterize(render_path, png_path):
                return True
        except Exception:
            logger.debug("rasterize poster failed", exc_info=True)
        return False
    finally:
        if tmp_decompressed and os.path.exists(tmp_decompressed):
            try:
                os.remove(tmp_decompressed)
            except OSError:
                pass


def _try_pyrender(glb_path: str, png_path: str) -> bool:
    import pyrender  # noqa: F811
    import trimesh

    # Load as a full scene so per-primitive materials, baseColor textures, and
    # vertex colours survive. force="mesh" (the old approach) concatenates every
    # primitive into one geometry and drops its materials, so pyrender rendered
    # the whole model in one default grey material — the "flat grey thumbnail"
    # bug — while the real viewer showed the textured GLB. from_trimesh_scene
    # rebuilds the pyrender scene node-for-node, keeping each mesh's material.
    loaded = trimesh.load(glb_path)
    if isinstance(loaded, trimesh.Trimesh):
        loaded = trimesh.Scene(loaded)
    if not isinstance(loaded, trimesh.Scene) or not loaded.geometry:
        return False

    scene = pyrender.Scene.from_trimesh_scene(
        loaded,
        bg_color=[0.96, 0.96, 0.96, 1.0],
        ambient_light=[0.35, 0.35, 0.35],
    )

    bounds = loaded.bounds
    if bounds is None:
        return False
    center = (bounds[0] + bounds[1]) / 2.0
    extent = float(np.ptp(bounds, axis=0).max())
    if extent < 1e-9:
        return False

    camera = pyrender.PerspectiveCamera(yfov=np.pi / 4.0)
    cam_dist = extent * 1.8
    cam_pose = np.eye(4)
    cam_pose[:3, 3] = center + np.array([cam_dist * 0.5, cam_dist * 0.3, cam_dist * 0.8])
    scene.add(camera, pose=cam_pose)

    # Key light aligned with the camera so the textured surface reads evenly;
    # ambient (set above) keeps shadowed faces from crushing to black.
    scene.add(pyrender.DirectionalLight(color=[1.0, 1.0, 1.0], intensity=3.0), pose=cam_pose)

    r = pyrender.OffscreenRenderer(*POSTER_SIZE)
    try:
        color, _ = r.render(scene)
    finally:
        r.delete()

    from PIL import Image
    img = Image.fromarray(color)
    Path(png_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(png_path, "PNG", optimize=True)
    return True


def _try_trimesh_scene(glb_path: str, png_path: str) -> bool:
    import trimesh

    scene = trimesh.load(glb_path)
    if isinstance(scene, trimesh.Trimesh):
        scene = trimesh.Scene([scene])
    data = scene.save_image(resolution=POSTER_SIZE)
    if not data or len(data) < 100:
        return False
    Path(png_path).parent.mkdir(parents=True, exist_ok=True)
    Path(png_path).write_bytes(data)
    return True


def _try_rasterize(glb_path: str, png_path: str) -> bool:
    """Software rasterizer using only trimesh + Pillow — no GL needed."""
    import trimesh
    from PIL import Image, ImageDraw

    mesh = trimesh.load(glb_path, force="mesh")
    if mesh.vertices.shape[0] == 0:
        return False

    verts = np.array(mesh.vertices, dtype=np.float64)
    faces = np.array(mesh.faces, dtype=np.int64)

    bounds = verts.min(axis=0), verts.max(axis=0)
    center = (bounds[0] + bounds[1]) / 2.0
    extent = (bounds[1] - bounds[0]).max()
    if extent < 1e-10:
        return False

    verts_centered = verts - center

    angle_y = np.radians(35)
    angle_x = np.radians(25)
    cy, sy = np.cos(angle_y), np.sin(angle_y)
    cx, sx = np.cos(angle_x), np.sin(angle_x)

    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    rot = rx @ ry
    verts_rot = verts_centered @ rot.T

    w, h = POSTER_SIZE
    margin = 0.12
    usable = min(w, h) * (1 - 2 * margin)
    proj_extent = max(
        verts_rot[:, 0].ptp(),
        verts_rot[:, 1].ptp(),
    )
    if proj_extent < 1e-10:
        return False
    scale = usable / proj_extent
    ox = w / 2.0
    oy = h / 2.0

    screen_x = verts_rot[:, 0] * scale + ox
    screen_y = -verts_rot[:, 1] * scale + oy

    face_z = verts_rot[faces, 2].mean(axis=1)
    order = np.argsort(face_z)

    light_dir = np.array([0.4, 0.6, 0.7])
    light_dir /= np.linalg.norm(light_dir)
    if hasattr(mesh, 'face_normals') and mesh.face_normals.shape[0] == faces.shape[0]:
        normals_rot = mesh.face_normals @ rot.T
    else:
        v0 = verts_rot[faces[:, 0]]
        v1 = verts_rot[faces[:, 1]]
        v2 = verts_rot[faces[:, 2]]
        normals_rot = np.cross(v1 - v0, v2 - v0)
        norms = np.linalg.norm(normals_rot, axis=1, keepdims=True)
        norms[norms < 1e-10] = 1.0
        normals_rot = normals_rot / norms

    dots = np.clip(np.sum(normals_rot * light_dir, axis=1), 0, 1)
    ambient = 0.35
    intensity = ambient + (1.0 - ambient) * dots

    base_r, base_g, base_b = MESH_COLOR[:3]
    face_colors = np.column_stack([
        np.clip(base_r * intensity, 0, 255).astype(np.uint8),
        np.clip(base_g * intensity, 0, 255).astype(np.uint8),
        np.clip(base_b * intensity, 0, 255).astype(np.uint8),
    ])

    img = Image.new("RGBA", POSTER_SIZE, BG_COLOR)
    draw = ImageDraw.Draw(img)

    for idx in order:
        f = faces[idx]
        tri = [
            (float(screen_x[f[0]]), float(screen_y[f[0]])),
            (float(screen_x[f[1]]), float(screen_y[f[1]])),
            (float(screen_x[f[2]]), float(screen_y[f[2]])),
        ]
        c = face_colors[idx]
        fill = (int(c[0]), int(c[1]), int(c[2]), 255)
        draw.polygon(tri, fill=fill)

    img = img.convert("RGB")
    Path(png_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(png_path, "PNG", optimize=True)
    return True
