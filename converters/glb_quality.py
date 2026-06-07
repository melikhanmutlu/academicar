"""GLB post-processing and quality checks for converted model assets."""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

from pygltflib import Buffer, BufferView, GLTF2, Material, PbrMetallicRoughness


class GLBQualityError(ValueError):
    """Raised when a converted GLB is not safe to publish."""


def _load_glb(path: str) -> GLTF2:
    if not os.path.exists(path):
        raise GLBQualityError("GLB output was not created.")
    if os.path.getsize(path) < 20:
        raise GLBQualityError("GLB output is empty or too small.")
    with open(path, "rb") as handle:
        if handle.read(4) != b"glTF":
            raise GLBQualityError("GLB header is invalid.")
    try:
        return GLTF2.load(path)
    except Exception as exc:  # pragma: no cover - exact parser messages vary
        raise GLBQualityError(f"GLB could not be parsed: {exc}") from exc


def _mime_for(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "image/png"


def _append_blob(gltf: GLTF2, payload: bytes) -> int:
    blob = gltf.binary_blob() or b""
    padding = b"\x00" * ((4 - (len(blob) % 4)) % 4)
    offset = len(blob) + len(padding)
    gltf.set_binary_blob(blob + padding + payload)
    if not gltf.buffers:
        gltf.buffers = [Buffer(byteLength=0)]
    gltf.buffers[0].byteLength = len(gltf.binary_blob() or b"")
    return offset


def _find_texture(uri: str, search_dirs: list[str]) -> Path | None:
    candidates = []
    uri_path = Path(uri.replace("\\", "/"))
    for directory in search_dirs:
        root = Path(directory).resolve()
        for candidate in (root / uri_path, root / uri_path.name):
            try:
                resolved = candidate.resolve()
            except (OSError, ValueError):
                continue
            if str(resolved).startswith(str(root)):
                candidates.append(resolved)
    lower_name = uri_path.name.lower()
    for directory in search_dirs:
        root = Path(directory)
        if root.exists():
            for entry in root.rglob("*"):
                if entry.is_file() and entry.name.lower() == lower_name:
                    candidates.append(entry)
                    break
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def embed_external_textures(glb_path: str, search_dirs: list[str] | None = None) -> bool:
    """Embed file-based image URIs into the GLB binary chunk.

    FBX2glTF and OBJ pipelines can leave image URIs as relative file paths,
    depending on source material layout. Public viewers and AR resolvers serve
    a single GLB file, so external image references must be packed into the GLB.
    """
    try:
        gltf = _load_glb(glb_path)
    except GLBQualityError:
        return False
    if not gltf.images:
        return False

    search_dirs = search_dirs or [os.path.dirname(glb_path)]
    if gltf.bufferViews is None:
        gltf.bufferViews = []

    changed = False
    for image in gltf.images:
        uri = image.uri
        if not uri or uri.startswith("data:") or image.bufferView is not None:
            continue
        texture_path = _find_texture(uri, search_dirs)
        if not texture_path:
            continue
        payload = texture_path.read_bytes()
        offset = _append_blob(gltf, payload)
        view = BufferView(buffer=0, byteOffset=offset, byteLength=len(payload))
        image.bufferView = len(gltf.bufferViews)
        image.mimeType = _mime_for(texture_path)
        image.uri = None
        gltf.bufferViews.append(view)
        changed = True

    if changed:
        gltf.save(glb_path)
    return changed


def has_base_color_textures(glb_path: str) -> bool:
    try:
        gltf = _load_glb(glb_path)
    except GLBQualityError:
        return False
    for material in gltf.materials or []:
        pbr = material.pbrMetallicRoughness
        if pbr and pbr.baseColorTexture is not None:
            return True
    return False


def repair_transparent_base_color(glb_path: str) -> bool:
    """Repair FBX2glTF's broken opacity-to-alpha mapping.

    FBX2glTF maps an FBX material's transparency onto baseColorFactor alpha and
    switches alphaMode to BLEND. When it cannot translate an opacity/transparency
    texture (it logs "Can't handle texture for TransparentColor; discarding") it
    leaves baseColorFactor alpha at 0.0 — which multiplies the whole material to
    fully transparent, so the surface is textured but invisible. This is the
    classic "FBX uploads render untextured" symptom: foliage/leaf cards and any
    material that carried an opacity channel vanish entirely.

    When a baseColorTexture is present, the texture's own alpha channel already
    provides any cutout, so a zero base-color factor alpha is never intended.
    Restore it to 1.0 so the material becomes visible while keeping the existing
    alphaMode (BLEND/MASK still honour per-texel texture alpha for cutouts).
    """
    try:
        gltf = _load_glb(glb_path)
    except GLBQualityError:
        return False

    changed = False
    for material in gltf.materials or []:
        pbr = material.pbrMetallicRoughness
        if not pbr or pbr.baseColorTexture is None:
            continue
        bcf = pbr.baseColorFactor
        if bcf and len(bcf) == 4 and bcf[3] == 0.0:
            bcf[3] = 1.0
            changed = True

    if changed:
        gltf.save(glb_path)
    return changed


def ensure_pbr_materials(glb_path: str) -> bool:
    """Ensure primitives have valid PBR materials without replacing artwork.

    Existing materials, textures, metallic/roughness values, and color factors
    are preserved. A neutral default material is only added for primitives that
    have no material assignment.
    """
    try:
        gltf = _load_glb(glb_path)
    except GLBQualityError:
        return False

    if gltf.materials is None:
        gltf.materials = []

    changed = False
    default_index = None
    for material in gltf.materials:
        if material.pbrMetallicRoughness is None:
            material.pbrMetallicRoughness = PbrMetallicRoughness(
                baseColorFactor=[0.8, 0.8, 0.8, 1.0],
                metallicFactor=0.0,
                roughnessFactor=0.75,
            )
            changed = True
        if material.doubleSided is not True:
            material.doubleSided = True
            changed = True

    for mesh in gltf.meshes or []:
        for primitive in mesh.primitives or []:
            if primitive.material is None or primitive.material >= len(gltf.materials):
                if default_index is None:
                    default_index = len(gltf.materials)
                    gltf.materials.append(
                        Material(
                            name="AcademicAR_Default",
                            pbrMetallicRoughness=PbrMetallicRoughness(
                                baseColorFactor=[0.8, 0.8, 0.8, 1.0],
                                metallicFactor=0.0,
                                roughnessFactor=0.75,
                            ),
                            doubleSided=True,
                        )
                    )
                primitive.material = default_index
                changed = True

    if changed:
        gltf.save(glb_path)
    return changed


def validate_glb_quality(glb_path: str) -> None:
    """Validate the minimum GLB quality needed for web and AR viewing."""
    gltf = _load_glb(glb_path)
    if not gltf.meshes:
        raise GLBQualityError("GLB contains no meshes.")

    primitive_count = 0
    for mesh in gltf.meshes or []:
        for primitive in mesh.primitives or []:
            primitive_count += 1
            if getattr(primitive.attributes, "POSITION", None) is None:
                raise GLBQualityError("A GLB primitive is missing POSITION data.")
            if primitive.material is None:
                raise GLBQualityError("A GLB primitive is missing a material.")
            if not gltf.materials or primitive.material >= len(gltf.materials):
                raise GLBQualityError("A GLB primitive references an invalid material.")
            material = gltf.materials[primitive.material]
            if material.pbrMetallicRoughness is None:
                raise GLBQualityError("A GLB material is missing PBR properties.")

    if primitive_count == 0:
        raise GLBQualityError("GLB contains no renderable primitives.")

    for image in gltf.images or []:
        if image.bufferView is None and image.uri:
            if image.uri.startswith("data:"):
                try:
                    base64.b64decode(image.uri.split(",", 1)[1], validate=True)
                except Exception as exc:
                    raise GLBQualityError("A GLB texture data URI is invalid.") from exc
            else:
                raise GLBQualityError(f"GLB still references an external texture: {image.uri}")
