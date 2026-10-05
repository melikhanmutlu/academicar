import subprocess
from pathlib import Path

import numpy as np
import pytest
import trimesh
from pygltflib import GLTF2

from converters.layers import normalize_layers
from converters.step_converter import STEPConverter

FIXTURES = Path(__file__).parent / "fixtures" / "step"
COLORED = FIXTURES / "assembly_colored.step"
SAME_COLOR = FIXTURES / "assembly_same_color.step"
BOX = FIXTURES / "box_plain.stp"


def _convert(source, tmp_path, **kwargs):
    converter = STEPConverter()
    output = tmp_path / "model.glb"
    return converter, output, converter.convert(str(source), str(output), **kwargs)


def _material_factors(glb):
    return {m.name: m.pbrMetallicRoughness.baseColorFactor for m in GLTF2.load(str(glb)).materials}


def _extents(glb):
    return np.ptp(trimesh.load(str(glb), force="scene").bounds, axis=0)


def test_colored_assembly_keeps_part_names_and_colours(tmp_path):
    converter, output, ok = _convert(COLORED, tmp_path)

    assert ok, converter.errors
    gltf = GLTF2.load(str(output))
    part_names = [n.name for n in gltf.nodes if n.mesh is not None]
    assert part_names == ["Housing", "Shaft", "Cap"]
    factors = np.array([m.pbrMetallicRoughness.baseColorFactor[:3] for m in gltf.materials])
    assert len(factors) == 3
    assert len({tuple(np.round(f, 2)) for f in factors}) == 3
    for expected in ([1.0, 0.0, 0.0], [0.0, 0.6, 0.0], [0.0, 0.0, 1.0]):
        assert any(np.allclose(f, expected, atol=0.02) for f in factors)


def test_assembly_is_y_up_in_metres(tmp_path):
    _, output, ok = _convert(COLORED, tmp_path)

    assert ok
    # 20 x 20 x 45 mm, tallest axis (STEP Z) must end up on glTF Y.
    assert _extents(output) == pytest.approx([0.020, 0.045, 0.020], abs=1e-4)


def test_stp_box_dimensions_after_rotation(tmp_path):
    _, output, ok = _convert(BOX, tmp_path)

    assert ok
    # STEP 10 x 20 x 30 (X, Y, Z) -> glTF X 0.01, Y 0.03, Z 0.02.
    assert _extents(output) == pytest.approx([0.010, 0.030, 0.020], abs=1e-4)
    assert [m.name for m in GLTF2.load(str(output)).materials] == ["Default"]


def test_uppercase_extension_accepted(tmp_path):
    source = tmp_path / "BOX.STP"
    source.write_bytes(BOX.read_bytes())

    converter, _, ok = _convert(source, tmp_path)

    assert ok, converter.errors


def test_color_applies_when_step_has_no_colours(tmp_path):
    converter, output, ok = _convert(SAME_COLOR, tmp_path, color="#B87333")

    assert ok, converter.errors
    expected = [0.4793, 0.1714, 0.0331]  # linear of #B87333
    factors = _material_factors(output)
    assert factors
    for factor in factors.values():
        assert factor[:3] == pytest.approx(expected, abs=1e-3)


def test_step_own_colours_win_over_color_parameter(tmp_path):
    converter, output, ok = _convert(COLORED, tmp_path, color="#B87333")

    assert ok, converter.errors
    reds = [f for f in _material_factors(output).values() if f[:3] == pytest.approx([1.0, 0.0, 0.0], abs=0.02)]
    assert len(reds) == 1


def test_uncolored_step_gets_default_gray_without_color(tmp_path):
    _, output, ok = _convert(SAME_COLOR, tmp_path)

    assert ok
    for factor in _material_factors(output).values():
        assert factor == pytest.approx([0.6038] * 3 + [1.0])


def test_every_primitive_has_a_material(tmp_path):
    _, output, ok = _convert(SAME_COLOR, tmp_path)

    gltf = GLTF2.load(str(output))
    assert ok and all(p.material is not None for m in gltf.meshes for p in m.primitives)


def test_invalid_header_rejected(tmp_path):
    source = tmp_path / "fake.step"
    source.write_text("this is not a step file\n" * 10)

    converter, output, ok = _convert(source, tmp_path)

    assert ok is False
    assert converter.errors == ["This file is not a valid STEP file (missing ISO-10303-21 header)."]
    assert not output.exists()


def test_bom_and_leading_whitespace_are_tolerated(tmp_path):
    source = tmp_path / "bom.stp"
    source.write_bytes(b"\xef\xbb\xbf\n  " + BOX.read_bytes())

    assert STEPConverter().validate(str(source)) is True


def test_wrong_extension_and_empty_file_rejected(tmp_path):
    wrong = tmp_path / "box.glb"
    wrong.write_bytes(BOX.read_bytes())
    empty = tmp_path / "empty.step"
    empty.write_bytes(b"")

    assert STEPConverter().validate(str(wrong)) is False
    assert STEPConverter().validate(str(empty)) is False


def test_child_process_failure_is_reported_friendly(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 2, "", "Traceback: OCC exploded " * 50)
    )

    converter, output, ok = _convert(BOX, tmp_path)

    assert ok is False
    assert converter.errors == ["STEP conversion failed. The file may be corrupt or use unsupported STEP features."]
    assert not output.exists()


def test_child_process_timeout(tmp_path, monkeypatch):
    def fake_run(command, **kw):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    converter, _, ok = _convert(BOX, tmp_path)

    assert ok is False
    assert "timed out" in converter.errors[0]


def test_failure_does_not_clobber_existing_output(tmp_path, monkeypatch):
    output = tmp_path / "model.glb"
    output.write_bytes(b"previous")
    monkeypatch.setattr(subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 2, "", "boom"))

    assert STEPConverter().convert(str(BOX), str(output)) is False
    assert output.read_bytes() == b"previous"


def test_corrupt_step_body_fails_cleanly(tmp_path):
    source = tmp_path / "broken.step"
    source.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n#1 = GARBAGE(;\nENDSEC;\nEND-ISO-10303-21;\n")

    converter, output, ok = _convert(source, tmp_path)

    assert ok is False and converter.errors
    assert not output.exists()
    assert not list(tmp_path.glob("*.tmp*"))


def test_triangle_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("STEP_MAX_TRIANGLES", "10")

    converter, output, ok = _convert(COLORED, tmp_path)

    assert ok is False
    assert "too detailed" in converter.errors[0]
    assert not output.exists()


def test_tolerance_env_overrides_reach_the_child(tmp_path, monkeypatch):
    seen = {}

    def fake_run(command, **kw):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 2, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("STEP_TOL_LINEAR", "0.5")
    monkeypatch.setenv("STEP_TOL_ANGULAR", "0.3")

    _convert(BOX, tmp_path)

    command = seen["command"]
    assert command[command.index("--tol-linear") + 1] == "0.5"
    assert command[command.index("--tol-angular") + 1] == "0.3"


@pytest.mark.parametrize("source, kwargs", [(COLORED, {}), (SAME_COLOR, {"color": "#B87333"})])
def test_layers_end_to_end(tmp_path, source, kwargs):
    _, output, ok = _convert(source, tmp_path, **kwargs)
    assert ok

    layers = normalize_layers(str(output))

    assert [l["name"] for l in layers] == ["Housing", "Shaft", "Cap"]
    assert [l["materials"] for l in layers] == [["Housing"], ["Shaft"], ["Cap"]]
    if source is COLORED:
        assert [l["color"] for l in layers][0] == "#ff0000"
    else:
        assert {l["color"] for l in layers} == {"#b87333"}
