"""Regression test: generate_poster must not silently fail on Draco-compressed
GLBs.

finalize_converted_glb() Draco-compresses every stored GLB via optimize_glb()
(see CLAUDE.md), but all three poster render backends (pyrender, trimesh
scene, the Pillow rasterizer) load geometry through trimesh, which cannot
decode KHR_draco_mesh_compression — the exact limitation the Blender USDZ
export path already works around with glb_has_draco()/decompress_glb(). Before
this fix, generate_poster() fed the Draco-compressed glb_path straight to all
three backends, so every successfully-converted model silently ended up with
no poster (reproduced via the admin 'Generate missing previews' action:
"Generated 0 missing preview(s). N could not be generated.").
"""
import json
import struct
from pathlib import Path
from unittest.mock import patch

import trimesh

from converters.glb_optimize import glb_has_draco


def _make_draco_flagged_glb(path: Path) -> str:
    """A minimal GLB whose JSON only declares Draco (no CLI / pygltflib
    needed) — enough to make glb_has_draco() true, matching
    test_v7_converter_improvements.TestGlbHasDraco.test_draco_extension_detected.
    """
    j = json.dumps({
        "asset": {"version": "2.0"},
        "extensionsUsed": ["KHR_draco_mesh_compression"],
    }).encode()
    j += b" " * ((4 - len(j) % 4) % 4)
    blob = b"glTF" + struct.pack("<I", 2) + struct.pack("<I", 12 + 8 + len(j))
    blob += struct.pack("<I", len(j)) + b"JSON" + j
    glb_path = str(path / "draco.glb")
    with open(glb_path, "wb") as fh:
        fh.write(blob)
    return glb_path


def _make_plain_glb(path: Path, name: str = "plain.glb") -> str:
    glb_path = str(path / name)
    mesh = trimesh.creation.box(extents=[1, 1, 1])
    trimesh.Scene([mesh]).export(glb_path)
    return glb_path


def test_draco_glb_is_decompressed_before_rendering(tmp_path):
    from converters.poster import generate_poster

    draco_glb = _make_draco_flagged_glb(tmp_path)
    assert glb_has_draco(draco_glb) is True
    plain_glb = _make_plain_glb(tmp_path)
    png_path = str(tmp_path / "poster.png")

    def fake_decompress(src_path, dst_path):
        # Stand-in for gltf-transform's real Draco decode.
        Path(dst_path).write_bytes(Path(plain_glb).read_bytes())
        return True

    with patch("converters.poster.decompress_glb", side_effect=fake_decompress) as mock_decompress:
        result = generate_poster(draco_glb, png_path)

    assert result is True, "poster generation must succeed once the Draco GLB is decompressed first"
    assert Path(png_path).is_file()
    mock_decompress.assert_called_once()
    called_src = mock_decompress.call_args[0][0]
    assert called_src == draco_glb

    # The scratch decompressed copy must not leak next to the original.
    leftovers = [p for p in tmp_path.iterdir() if "posterdecomp" in p.name]
    assert leftovers == [], f"decompressed scratch file was not cleaned up: {leftovers}"


def test_draco_glb_without_working_decompressor_still_fails_gracefully(tmp_path):
    """If gltf-transform is unavailable, generate_poster must return False
    instead of raising — matches the pre-existing best-effort contract."""
    from converters.poster import generate_poster

    draco_glb = _make_draco_flagged_glb(tmp_path)
    png_path = str(tmp_path / "poster.png")

    with patch("converters.poster.decompress_glb", return_value=False):
        result = generate_poster(draco_glb, png_path)

    assert result is False
    assert not Path(png_path).exists()


def test_non_draco_glb_skips_decompression(tmp_path):
    """Plain (non-Draco) GLBs must render directly without ever invoking
    decompress_glb — no behavior change for the common local/SQLite path."""
    from converters.poster import generate_poster

    plain_glb = _make_plain_glb(tmp_path)
    png_path = str(tmp_path / "poster.png")

    with patch("converters.poster.decompress_glb") as mock_decompress:
        result = generate_poster(plain_glb, png_path)

    assert result is True
    mock_decompress.assert_not_called()
