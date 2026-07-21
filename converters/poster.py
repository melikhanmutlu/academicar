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
    """Generate a poster image from a GLB file. Returns True on success."""
    return generate_poster_backend(glb_path, png_path) is not None


def generate_poster_backend(glb_path: str, png_path: str) -> str | None:
    """Generate a poster and return the name of the backend that produced it.

    Returns "pyrender" (full PBR + textures/vertex colours), "trimesh" (pyglet
    software render), "rasterize" (flat-shaded no-material fallback), or None if
    every backend failed. The backend name is what tells us on a live deploy
    whether posters are the rich textured render or the flat-grey fallback —
    surfaced by the admin regenerate flow so a grey thumbnail can be diagnosed
    (OSMesa/pyrender unavailable) without guessing.

    All three render backends load geometry via trimesh, which cannot decode
    Draco-compressed meshes (see finalize_converted_glb / optimize_glb) — the
    same limitation the Blender USDZ export path already works around. When
    the stored GLB is Draco-compressed, render from a decompressed scratch
    copy instead so poster generation doesn't silently fail for every
    optimized model.
    """
    if not os.path.isfile(glb_path):
        return None
    render_path = glb_path
    tmp_decompressed = None
    if glb_has_draco(glb_path):
        tmp_decompressed = f"{glb_path}.posterdecomp.{os.getpid()}.{threading.get_ident()}.glb"
        if decompress_glb(glb_path, tmp_decompressed):
            render_path = tmp_decompressed
        else:
            tmp_decompressed = None
    try:
        for name, backend in (
            ("pyrender", _try_pyrender),
            ("trimesh", _try_trimesh_scene),
            ("rasterize", _try_rasterize),
        ):
            try:
                if backend(render_path, png_path):
                    logger.info("poster generated via %s backend for %s", name, glb_path)
                    return name
            except Exception:
                logger.debug("%s poster backend failed", name, exc_info=True)
        return None
    finally:
        if tmp_decompressed and os.path.exists(tmp_decompressed):
            try:
                os.remove(tmp_decompressed)
            except OSError:
                pass


def _bake_textures_to_vertex_colors(scene) -> None:
    """Replace each textured geometry's visual with texture-sampled vertex colours.

    OSMesa (the headless software GL backend) does not sample image textures, so
    baseColorTexture-driven colour is lost in the pyrender poster. trimesh's
    ``TextureVisuals.to_color()`` samples the texture at each vertex UV, producing
    ColorVisuals that the software renderer CAN draw. Per-vertex sampling is
    coarser than true texturing but captures the model's colours — enough for a
    thumbnail. Best-effort per geometry: any failure leaves that mesh untouched.
    """
    import trimesh

    for name, geom in list(scene.geometry.items()):
        visual = getattr(geom, "visual", None)
        if not isinstance(visual, trimesh.visual.TextureVisuals):
            continue
        material = getattr(visual, "material", None)
        has_texture = getattr(visual, "uv", None) is not None and (
            getattr(material, "baseColorTexture", None) is not None
            or getattr(material, "image", None) is not None
        )
        if not has_texture:
            continue
        try:
            colored = visual.to_color()
            if getattr(colored, "vertex_colors", None) is not None and len(colored.vertex_colors):
                geom.visual = colored
        except Exception:
            logger.debug("texture->vertex-colour bake failed for %s", name, exc_info=True)


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

    # Bake image (baseColor) textures into per-vertex colours. pyrender's OSMesa
    # software backend — the only GL available on the headless host — does not
    # sample image textures (it falls back to the flat grey baseColorFactor), so
    # a texture-coloured model rendered grey even though model-viewer shows it in
    # full colour. Vertex colours DO render under OSMesa, and trimesh can sample
    # the texture per vertex, so this transfers the artwork into a form the
    # software renderer can draw. Untextured / vertex-colour / factor-only
    # geometry is left untouched.
    _bake_textures_to_vertex_colors(loaded)

    # Brighter ambient + a key/fill/back light rig approximate model-viewer's
    # neutral studio environment: pyrender has no image-based lighting, so a
    # single dim light left colourless models (STL exports carry no material)
    # looking muddy grey next to the viewer. This lifts them toward a clean,
    # evenly-lit look while still shaping textured/coloured models.
    scene = pyrender.Scene.from_trimesh_scene(
        loaded,
        bg_color=[0.96, 0.96, 0.96, 1.0],
        ambient_light=[0.5, 0.5, 0.5],
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

    # Key from the camera, fill from the opposite side, soft back light — so
    # shadowed faces stay legible instead of crushing to black.
    for direction, intensity in (
        ([0.5, 0.4, 0.8], 3.5),
        ([-0.6, 0.2, 0.4], 2.0),
        ([0.1, -0.5, -0.6], 1.5),
    ):
        light_pose = np.eye(4)
        light_pose[:3, 3] = center + np.array(direction) * cam_dist
        scene.add(pyrender.DirectionalLight(color=[1.0, 1.0, 1.0], intensity=intensity), pose=light_pose)

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


def _vertex_colors_for(geom):
    """Return an (N, 3) uint8 per-vertex colour array for a trimesh geometry.

    Resolves colour from whatever the material carries, in priority order:
    explicit vertex colours -> baseColorTexture sampled per vertex -> flat
    baseColorFactor -> the neutral default. Lets the software rasterizer paint a
    model in its real colours (the deployed host has no working OSMesa, so the
    pyrender path never runs and this fallback is what actually renders posters).
    """
    import numpy as _np
    import trimesh

    n = len(geom.vertices)
    default = _np.tile(_np.array(MESH_COLOR[:3], dtype=_np.uint8), (n, 1))
    visual = getattr(geom, "visual", None)
    if visual is None:
        return default

    # Explicit vertex colours (ColorVisuals) win.
    kind = getattr(visual, "kind", None)
    if kind == "vertex":
        try:
            vc = _np.asarray(visual.vertex_colors)[:, :3].astype(_np.uint8)
            if len(vc) == n:
                return vc
        except Exception:
            pass

    material = getattr(visual, "material", None)
    # Sample a baseColorTexture per vertex.
    if isinstance(visual, trimesh.visual.TextureVisuals) and getattr(visual, "uv", None) is not None and (
        getattr(material, "baseColorTexture", None) is not None or getattr(material, "image", None) is not None
    ):
        try:
            vc = _np.asarray(visual.to_color().vertex_colors)[:, :3].astype(_np.uint8)
            if len(vc) == n:
                return vc
        except Exception:
            pass

    # Flat baseColorFactor. glTF stores it as 0..1 floats, but trimesh often
    # returns it as 0..255 after a GLB round-trip — handle both scales.
    bcf = getattr(material, "baseColorFactor", None)
    if bcf is not None:
        try:
            arr = _np.asarray(bcf[:3], dtype=_np.float64)
            if arr.max() <= 1.0:
                arr = arr * 255.0
            rgb = _np.clip(arr, 0, 255).astype(_np.uint8)
            return _np.tile(rgb, (n, 1))
        except Exception:
            pass

    # Trimesh's main colour (e.g. a single main_color) as a last resort.
    main = getattr(material, "main_color", None)
    if main is not None:
        try:
            rgb = _np.asarray(main[:3], dtype=_np.uint8)
            return _np.tile(rgb, (n, 1))
        except Exception:
            pass
    return default


def _colored_mesh_from_glb(glb_path: str):
    """Load a GLB as a single mesh with per-vertex colours and baked transforms.

    Unlike ``trimesh.load(force="mesh")`` (which concatenates geometry and drops
    per-material colour), this walks the scene, resolves each geometry's real
    colours, bakes the node transforms, and concatenates — so the rasterizer sees
    both correct world positions and correct colours.
    """
    import numpy as _np
    import trimesh

    loaded = trimesh.load(glb_path)
    if isinstance(loaded, trimesh.Trimesh):
        loaded = trimesh.Scene(loaded)
    if not isinstance(loaded, trimesh.Scene) or not loaded.geometry:
        return None

    all_verts, all_faces, all_colors = [], [], []
    offset = 0
    for node_name in loaded.graph.nodes_geometry:
        transform, geom_name = loaded.graph[node_name]
        geom = loaded.geometry.get(geom_name)
        if geom is None or len(geom.vertices) == 0 or len(geom.faces) == 0:
            continue
        verts = trimesh.transformations.transform_points(_np.asarray(geom.vertices), transform)
        all_verts.append(verts)
        all_faces.append(_np.asarray(geom.faces, dtype=_np.int64) + offset)
        all_colors.append(_vertex_colors_for(geom))
        offset += len(geom.vertices)

    if not all_verts:
        return None
    mesh = trimesh.Trimesh(
        vertices=_np.vstack(all_verts),
        faces=_np.vstack(all_faces),
        vertex_colors=_np.vstack(all_colors),
        process=False,
    )
    return mesh


def _try_rasterize(glb_path: str, png_path: str) -> bool:
    """Software rasterizer using only trimesh + Pillow — no GL needed.

    This is the reliable fallback (and, on hosts without a working OSMesa GL, the
    ONLY backend): it renders the model in its real colours via per-vertex
    colours resolved from materials/textures, shaded with a simple Lambertian
    term — no OpenGL, so it works everywhere.
    """
    from PIL import Image, ImageDraw

    mesh = _colored_mesh_from_glb(glb_path)
    if mesh is None or mesh.vertices.shape[0] == 0:
        return False

    verts = np.array(mesh.vertices, dtype=np.float64)
    faces = np.array(mesh.faces, dtype=np.int64)
    try:
        vertex_colors = np.asarray(mesh.visual.vertex_colors)[:, :3].astype(np.float64)
    except Exception:
        vertex_colors = np.tile(np.array(MESH_COLOR[:3], dtype=np.float64), (len(verts), 1))

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
        np.ptp(verts_rot[:, 0]),
        np.ptp(verts_rot[:, 1]),
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
    ambient = 0.45
    intensity = ambient + (1.0 - ambient) * dots

    # Per-face base colour = mean of its vertices' colours (the model's real
    # material/texture colour), then shaded by the Lambertian term.
    face_base = vertex_colors[faces].mean(axis=1)  # (F, 3) float
    face_colors = np.clip(face_base * intensity[:, None], 0, 255).astype(np.uint8)

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
