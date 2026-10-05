"""Tests for converters/medical (DICOM series + segmentation -> layered GLB), on synthetic data."""

import json
import logging
import math
import os
import subprocess
import zipfile

import nibabel as nib
import numpy as np
import pygltflib
import pytest
import trimesh

from converters.medical import MAX_LAYERS, MEDICAL_PRESETS, MedicalConverter, detect_medical_format
from converters.medical.cli import run_conversion
from converters.medical.segmentation import cielab_dicom_to_hex
from tests.medical_fixtures import (
    PATIENT_ID,
    PATIENT_NAME,
    cielab_scaled,
    sphere,
    write_ct_series,
    write_dicom_seg,
    zip_folder,
    zip_folders,
)


# ------------------------------------------------------------------ helpers

def layer_meshes(glb_path):
    """{layer name: Trimesh in world (glTF) coordinates}."""
    scene = trimesh.load(glb_path, force="scene")
    out = {}
    for node in scene.graph.nodes_geometry:
        transform, geom_name = scene.graph[node]
        out[node] = scene.geometry[geom_name].copy().apply_transform(transform)
    return out


def components(mesh):
    """Connected pieces of a mesh, largest volume first."""
    return sorted(mesh.split(only_watertight=False), key=lambda m: -abs(m.volume))


def make_ct_zip(tmp_path, name="ct", **kwargs):
    folder = write_ct_series(tmp_path / name, **kwargs)
    return zip_folder(folder, tmp_path / f"{name}.zip")


def convert_in_process(kind, src, tmp_path, preset=None):
    out = str(tmp_path / "out" / "model.glb")
    return run_conversion(kind, str(src), out, preset), out


def save_nifti(path, data, affine=None):
    nib.save(nib.Nifti1Image(data, np.eye(4) if affine is None else affine), str(path))
    return str(path)


def label_volume(*specs, shape=(64, 64, 64), dtype=np.uint8):
    """specs: (label, centre_voxel, radius) -> integer label map."""
    vol = np.zeros(shape, dtype=dtype)
    for label, centre, radius in specs:
        vol[sphere(shape, centre, radius)] = label
    return vol


def centroid(mesh):
    return mesh.bounds.mean(axis=0)


# ------------------------------------------------------------- DICOM series

SPHERE_ML = 4 / 3 * math.pi * 15**3 / 1000


def test_bone_preset_volume_extent_and_material(tmp_path):
    result, out = convert_in_process("dicom", make_ct_zip(tmp_path), tmp_path, "bone")
    assert result["ok"], result["error"]
    assert result["modality"] == "CT"
    assert [l["name"] for l in result["layers"]] == ["Bone"]
    assert result["layers"][0]["color"] == "#E8D5B7"
    assert result["layers"][0]["volume_ml"] == pytest.approx(SPHERE_ML, rel=0.15)

    mesh = layer_meshes(out)["Bone"]
    assert mesh.extents == pytest.approx([0.03] * 3, rel=0.15)
    assert mesh.bounds.mean(axis=0) == pytest.approx([0, 0, 0], abs=1e-3)  # centred
    assert mesh.volume > 0  # outward-facing

    gltf = pygltflib.GLTF2().load(out)
    assert [m.name for m in gltf.materials] == ["Bone"]
    assert [n.name for n in gltf.nodes if n.mesh is not None] == ["Bone"]
    factor = gltf.materials[0].pbrMetallicRoughness.baseColorFactor
    # glTF baseColorFactor is linear: #E8D5B7 -> (0.807, 0.665, 0.474)
    assert factor == pytest.approx([0.807, 0.665, 0.474, 1.0], abs=0.002)
    assert gltf.materials[0].pbrMetallicRoughness.metallicFactor == 0.0
    assert gltf.materials[0].doubleSided is False


def test_dicom_orientation_maps_to_gltf_axes(tmp_path):
    # Big sphere low/anterior-ish, small sphere superior + posterior + left of it.
    spheres = [(20.0, 20.0, 25.0, 12.0), (44.0, 44.0, 55.0, 6.0)]
    result, out = convert_in_process("dicom", make_ct_zip(tmp_path, spheres=spheres), tmp_path, "bone")
    assert result["ok"], result["error"]
    big, small = components(layer_meshes(out)["Bone"])[:2]
    assert big.volume > small.volume
    cb, cs = centroid(big), centroid(small)
    assert cs[1] > cb[1] + 0.02  # higher z (superior) -> higher glTF Y
    assert cs[2] < cb[2] - 0.015  # more posterior y (LPS) -> lower glTF Z
    assert cs[0] > cb[0] + 0.015  # more Left (LPS x) -> higher glTF X


def test_implicit_vr_series_reads(tmp_path):
    zip_path = make_ct_zip(tmp_path, implicit=True)
    result, _ = convert_in_process("dicom", zip_path, tmp_path, "bone")
    assert result["ok"], result["error"]


def test_skin_and_auto_presets(tmp_path):
    zip_path = make_ct_zip(tmp_path, background=-1000, fg=40)  # soft tissue sphere
    skin, _ = convert_in_process("dicom", zip_path, tmp_path / "a", "skin")
    assert skin["ok"], skin["error"]
    assert skin["layers"][0]["name"] == "Skin"
    assert skin["layers"][0]["color"] == "#FFCBA4"
    assert skin["layers"][0]["volume_ml"] == pytest.approx(SPHERE_ML, rel=0.15)

    auto, _ = convert_in_process("dicom", zip_path, tmp_path / "b", "auto")
    assert auto["ok"], auto["error"]
    assert auto["layers"][0]["name"] == "Auto threshold"
    assert auto["layers"][0]["volume_ml"] == pytest.approx(SPHERE_ML, rel=0.15)

    # 40 HU is below the bone threshold -> nothing to show
    bone, _ = convert_in_process("dicom", zip_path, tmp_path / "c", "bone")
    assert not bone["ok"]
    assert bone["error"] == "No structure matched the selected preset in this scan."


def test_contrast_preset_includes_bone_range(tmp_path):
    zip_path = make_ct_zip(tmp_path, fg=200)  # contrast-filled vessel level, below the bone threshold
    result, _ = convert_in_process("dicom", zip_path, tmp_path / "a", "contrast")
    assert result["ok"], result["error"]
    assert result["layers"][0]["color"] == "#CC2222"
    assert not convert_in_process("dicom", zip_path, tmp_path / "b", "bone")[0]["ok"]


def test_mr_series_falls_back_to_auto_with_note(tmp_path):
    zip_path = make_ct_zip(tmp_path, modality="MR", background=0, fg=800)
    result, _ = convert_in_process("dicom", zip_path, tmp_path, "bone")
    assert result["ok"], result["error"]
    assert result["modality"] == "MR"
    assert result["layers"][0]["name"] == "Auto threshold"
    assert any("not CT" in n and "automatic threshold" in n for n in result["notes"])


def test_presets_are_documented():
    assert set(MEDICAL_PRESETS) == {"bone", "skin", "contrast", "auto"}
    for preset in MEDICAL_PRESETS.values():
        assert preset["label"] and preset["description"] and preset["color"].startswith("#")
    assert "bone" in MEDICAL_PRESETS["contrast"]["description"].lower()


def test_undecodable_compression_is_explained(tmp_path):
    # The fixture wraps raw pixels in a JPEG 2000 container, which no decoder can read.
    result, _ = convert_in_process("dicom", make_ct_zip(tmp_path, compressed=True), tmp_path, "bone")
    assert not result["ok"]
    assert "cannot be decoded" in result["error"] and "JPEG 2000" in result["error"]


@pytest.mark.parametrize("syntax", ["JPEGLosslessProcess14_1", "JPEGLSLossless", "JPEG2000Lossless", "RLELossless"])
def test_compressed_series_convert_like_uncompressed(tmp_path, syntax):
    from tests.medical_fixtures import compress_series

    plain, _ = convert_in_process("dicom", make_ct_zip(tmp_path / "plain"), tmp_path / "plain", "bone")
    folder = write_ct_series(tmp_path / "packed" / "series")
    compress_series(folder, syntax)
    packed, _ = convert_in_process("dicom", zip_folder(folder, tmp_path / "packed.zip"), tmp_path / "packed", "bone")
    assert packed["ok"], packed["error"]
    assert packed["layers"][0]["volume_ml"] == plain["layers"][0]["volume_ml"]


def test_too_few_slices_rejected(tmp_path):
    result, _ = convert_in_process("dicom", make_ct_zip(tmp_path, n_slices=2), tmp_path, "bone")
    assert not result["ok"]
    assert "At least 3 slices" in result["error"]


def test_missing_slice_rejected(tmp_path):
    result, _ = convert_in_process("dicom", make_ct_zip(tmp_path, skip=(20,)), tmp_path, "bone")
    assert not result["ok"]
    assert result["error"] == "Some slices are missing from this series."


def test_gantry_tilt_rejected(tmp_path):
    import pydicom

    folder = write_ct_series(tmp_path / "tilt")
    for i, name in enumerate(sorted(os.listdir(folder))):
        path = os.path.join(folder, name)
        ds = pydicom.dcmread(path)
        ds.ImagePositionPatient = [0, i * 0.5, 2 * i]  # 14 degree shear
        ds.save_as(path)
    result, _ = convert_in_process("dicom", zip_folder(folder, tmp_path / "tilt.zip"), tmp_path, "bone")
    assert not result["ok"]
    assert "gantry tilt" in result["error"]


def test_not_a_dicom_zip_gives_friendly_error(tmp_path):
    zip_path = tmp_path / "junk.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(4):
            zf.writestr(f"file{i}.txt", "hello")
    result, _ = convert_in_process("dicom", zip_path, tmp_path, "bone")
    assert not result["ok"]
    assert "No readable DICOM" in result["error"]


def test_zip_bomb_guard(tmp_path, monkeypatch):
    zip_path = make_ct_zip(tmp_path)
    monkeypatch.setenv("MEDICAL_MAX_UNZIPPED_BYTES", "10000")
    result, _ = convert_in_process("dicom", zip_path, tmp_path, "bone")
    assert not result["ok"]
    assert "too large" in result["error"]
    assert detect_medical_format(zip_path, "scan.zip")[1] is not None
    # nothing is left behind next to the output
    assert [p for p in os.listdir(tmp_path / "out") if p.startswith(".medical_")] == []


def test_too_many_files_guard(tmp_path, monkeypatch):
    zip_path = make_ct_zip(tmp_path)
    monkeypatch.setenv("MEDICAL_MAX_FILES", "10")
    result, _ = convert_in_process("dicom", zip_path, tmp_path, "bone")
    assert not result["ok"]
    assert "too many files" in result["error"]


def test_voxel_cap_applies_stride_and_note(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDICAL_MAX_VOXELS", "30000")  # 64*64*40 = 163840
    result, out = convert_in_process("dicom", make_ct_zip(tmp_path), tmp_path, "bone")
    assert result["ok"], result["error"]
    assert any(n.startswith("Downsampled to") and "mm voxels" in n for n in result["notes"])
    assert max(result["voxel_mm"]) > 1.5
    assert result["layers"][0]["volume_ml"] == pytest.approx(SPHERE_ML, rel=0.25)
    assert layer_meshes(out)["Bone"].extents == pytest.approx([0.03] * 3, rel=0.25)


def test_face_cap_retries_with_larger_stride(tmp_path, monkeypatch):
    zip_path = make_ct_zip(tmp_path)
    full, full_out = convert_in_process("dicom", zip_path, tmp_path / "a", "bone")
    assert full["ok"] and len(layer_meshes(full_out)["Bone"].faces) > 3000
    monkeypatch.setenv("MEDICAL_MAX_FACES", "3000")
    result, out = convert_in_process("dicom", zip_path, tmp_path / "b", "bone")
    assert result["ok"], result["error"]
    assert len(layer_meshes(out)["Bone"].faces) <= 3000
    assert any("manageable size" in n for n in result["notes"])
    monkeypatch.setenv("MEDICAL_MAX_FACES", "10")
    result, _ = convert_in_process("dicom", zip_path, tmp_path / "c", "bone")
    assert not result["ok"] and "too complex" in result["error"]


# ------------------------------------------------------------- segmentation

def test_nifti_multilabel_ras_mapping(tmp_path):
    vol = label_volume((1, (20, 20, 20), 8), (2, (44, 44, 44), 8))
    path = save_nifti(tmp_path / "seg.nii.gz", vol)
    result, out = convert_in_process("segmentation", path, tmp_path)
    assert result["ok"], result["error"]
    layers = result["layers"]
    assert [l["name"] for l in layers] == ["Label 1", "Label 2"]
    assert layers[0]["color"] != layers[1]["color"]
    meshes = layer_meshes(out)
    a, b = centroid(meshes["Label 1"]), centroid(meshes["Label 2"])
    assert b[0] < a[0] - 0.02  # RAS +x is Right -> glTF X = -x
    assert b[1] > a[1] + 0.02  # superior -> +Y
    assert b[2] > a[2] + 0.02  # anterior -> +Z
    # each label is its own material, named like the node
    gltf = pygltflib.GLTF2().load(out)
    assert sorted(m.name for m in gltf.materials) == ["Label 1", "Label 2"]
    assert layers[0]["volume_ml"] == pytest.approx(4 / 3 * math.pi * 8**3 / 1000, rel=0.15)


def test_nifti_oblique_affine_is_respected(tmp_path):
    # voxel i -> +y (anterior), voxel j -> -x (left), k -> +z: a 90 degree turn in the axial plane.
    affine = np.array([[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float)
    vol = label_volume((1, (20, 32, 32), 8), (2, (44, 32, 32), 8))
    path = save_nifti(tmp_path / "oblique.nii", vol, affine)
    result, out = convert_in_process("segmentation", path, tmp_path)
    assert result["ok"], result["error"]
    meshes = layer_meshes(out)
    a, b = centroid(meshes["Label 1"]), centroid(meshes["Label 2"])
    assert b[2] > a[2] + 0.02  # larger i = more anterior = +Z
    assert abs(b[0] - a[0]) < 0.005 and abs(b[1] - a[1]) < 0.005


def test_nifti_without_extension_and_4d_handling(tmp_path):
    vol = label_volume((1, (32, 32, 32), 10))
    odd = tmp_path / "upload.bin"
    save_nifti(tmp_path / "tmp.nii", vol)
    os.replace(tmp_path / "tmp.nii", odd)
    assert convert_in_process("segmentation", odd, tmp_path / "a")[0]["ok"]

    squeezed = save_nifti(tmp_path / "s.nii.gz", vol[..., None])
    assert convert_in_process("segmentation", squeezed, tmp_path / "b")[0]["ok"]

    four_d = save_nifti(tmp_path / "t.nii.gz", np.stack([vol, vol], axis=-1))
    result, _ = convert_in_process("segmentation", four_d, tmp_path / "c")
    assert not result["ok"] and "4D" in result["error"]

    empty = save_nifti(tmp_path / "e.nii.gz", np.zeros((16, 16, 16), np.uint8))
    result, _ = convert_in_process("segmentation", empty, tmp_path / "d")
    assert not result["ok"] and "No structures" in result["error"]


def test_nifti_float_binary_mask_and_intensity_image(tmp_path):
    mask = sphere((64, 64, 64), (32, 32, 32), 10).astype(np.float32)
    ok, _ = convert_in_process("segmentation", save_nifti(tmp_path / "m.nii.gz", mask), tmp_path / "a")
    assert ok["ok"] and len(ok["layers"]) == 1
    image = np.random.default_rng(0).normal(100, 30, (16, 16, 16)).astype(np.float32)
    bad, _ = convert_in_process("segmentation", save_nifti(tmp_path / "i.nii.gz", image), tmp_path / "b")
    assert not bad["ok"] and "does not look like a segmentation" in bad["error"]


def test_zip_of_binary_masks_named_from_files(tmp_path):
    liver = sphere((64, 64, 64), (20, 32, 32), 9).astype(np.uint8)
    kidney = sphere((64, 64, 64), (44, 32, 32), 7).astype(np.uint8)
    empty = np.zeros((64, 64, 64), np.uint8)
    zip_path = tmp_path / "masks.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, vol in (("liver", liver), ("kidney_left", kidney), ("spleen", empty)):
            nii = save_nifti(tmp_path / f"{name}.nii.gz", vol)
            zf.write(nii, f"segmentations/{name}.nii.gz")
        zf.writestr("__MACOSX/._liver.nii.gz", b"junk")
    result, out = convert_in_process("segmentation", zip_path, tmp_path)
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Kidney left", "Liver"]  # natural file order, empty skipped
    assert sorted(m.name for m in pygltflib.GLTF2().load(out).materials) == ["Kidney left", "Liver"]


def test_zip_of_masks_with_mismatched_grids_rejected(tmp_path):
    zip_path = tmp_path / "masks.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(save_nifti(tmp_path / "a.nii.gz", np.ones((8, 8, 8), np.uint8)), "a.nii.gz")
        zf.write(save_nifti(tmp_path / "b.nii.gz", np.ones((9, 8, 8), np.uint8)), "b.nii.gz")
    result, _ = convert_in_process("segmentation", zip_path, tmp_path)
    assert not result["ok"]
    assert "same size and orientation" in result["error"]


def test_layer_cap_keeps_largest(tmp_path):
    vol = np.zeros((64, 64, 8), np.uint8)
    n = MAX_LAYERS + 6
    for i in range(n):
        x, y = 3 + (i % 10) * 6, 3 + (i // 10) * 6
        size = 1 if i < 6 else 2  # the first six are the smallest
        vol[x : x + size + 2, y : y + size + 2, 2:6] = i + 1
    result, _ = convert_in_process("segmentation", save_nifti(tmp_path / "many.nii.gz", vol), tmp_path)
    assert result["ok"], result["error"]
    assert len(result["layers"]) <= MAX_LAYERS
    assert any(str(MAX_LAYERS) in note for note in result["notes"])
    names = {l["name"] for l in result["layers"]}
    assert "Label 1" not in names and f"Label {n}" in names


def test_seg_nrrd_names_and_colours_from_header(tmp_path):
    import nrrd

    vol = label_volume((1, (20, 20, 20), 8), (3, (44, 44, 44), 8))
    header = {
        "space": "left-posterior-superior",
        "space directions": np.eye(3),
        "space origin": np.zeros(3),
        "kinds": ["domain", "domain", "domain"],
        "Segment0_Name": "Left lung",
        "Segment0_Color": "0.2 0.4 0.6",
        "Segment0_LabelValue": "1",
        "Segment0_Layer": "0",
        "Segment1_Name": "Tumour",
        "Segment1_Color": "1 0 0",
        "Segment1_LabelValue": "3",
        "Segment1_Layer": "0",
    }
    path = str(tmp_path / "case.seg.nrrd")
    nrrd.write(path, vol, header)
    result, out = convert_in_process("segmentation", path, tmp_path)
    assert result["ok"], result["error"]
    assert [(l["name"], l["color"]) for l in result["layers"]] == [("Left lung", "#336699"), ("Tumour", "#FF0000")]
    meshes = layer_meshes(out)
    a, b = centroid(meshes["Left lung"]), centroid(meshes["Tumour"])
    assert b[0] > a[0] + 0.02  # LPS: larger x = more Left = +X
    assert b[2] < a[2] - 0.02  # LPS: larger y = more posterior = -Z
    assert b[1] > a[1] + 0.02


def test_seg_nrrd_overlapping_layers_and_ras_space(tmp_path):
    import nrrd

    first = sphere((64, 64, 64), (32, 32, 32), 12)
    second = sphere((64, 64, 64), (32, 32, 32), 6)  # overlaps the first (separate layer)
    data = np.stack([first, second]).astype(np.uint8)
    header = {
        "space": "right-anterior-superior",
        "space directions": np.array([[np.nan] * 3, [1, 0, 0], [0, 1, 0], [0, 0, 1]]),
        "space origin": np.zeros(3),
        "kinds": ["list", "domain", "domain", "domain"],
        "Segment0_Name": "Outer",
        "Segment0_LabelValue": "1",
        "Segment0_Layer": "0",
        "Segment1_Name": "Inner",
        "Segment1_LabelValue": "1",
        "Segment1_Layer": "1",
    }
    path = str(tmp_path / "overlap.seg.nrrd")
    nrrd.write(path, data, header)
    result, _ = convert_in_process("segmentation", path, tmp_path)
    assert result["ok"], result["error"]
    names = [l["name"] for l in result["layers"]]
    assert names == ["Outer", "Inner"]
    assert result["layers"][0]["volume_ml"] > result["layers"][1]["volume_ml"]
    assert not result["notes"]  # explicit RAS space: no assumption note


def test_plain_nrrd_without_space_assumes_lps_with_note(tmp_path):
    import nrrd

    vol = label_volume((1, (32, 32, 32), 10))
    path = str(tmp_path / "plain.nrrd")
    nrrd.write(path, vol, {"space directions": np.eye(3), "kinds": ["domain"] * 3})
    result, _ = convert_in_process("segmentation", path, tmp_path)
    assert result["ok"], result["error"]
    assert any("assumed LPS" in n for n in result["notes"])


def test_dicom_seg_names_colours_and_orientation(tmp_path):
    shape = (64, 64, 40)
    low = sphere(shape, (20, 20, 20), 8, (1, 1, 2))
    high = sphere(shape, (44, 44, 56), 8, (1, 1, 2))
    red = cielab_scaled(54.2917, 80.8125, 69.8851)  # sRGB (255, 0, 0) in D50 CIELab
    path = write_dicom_seg(tmp_path / "seg.dcm", [low, high], ["Liver", "Kidney"], [red, None], shape)
    assert detect_medical_format(path, "seg.dcm") == ("segmentation", None)
    result, out = convert_in_process("segmentation", path, tmp_path)
    assert result["ok"], result["error"]
    layers = result["layers"]
    assert [l["name"] for l in layers] == ["Liver", "Kidney"]
    r, g, b = (int(layers[0]["color"][i : i + 2], 16) for i in (1, 3, 5))
    assert (r, g, b) == pytest.approx((255, 0, 0), abs=4)
    assert layers[1]["color"] != layers[0]["color"]  # no colour -> palette
    meshes = layer_meshes(out)
    a, b_ = centroid(meshes["Liver"]), centroid(meshes["Kidney"])
    assert b_[1] > a[1] + 0.03  # higher z -> higher Y
    assert b_[2] < a[2] - 0.02  # higher LPS y -> lower Z
    assert layers[0]["volume_ml"] == pytest.approx(4 / 3 * math.pi * 8**3 / 1000, rel=0.2)


def test_cielab_conversion_known_values():
    assert cielab_dicom_to_hex(cielab_scaled(100, 0, 0)) == "#FFFFFF"
    assert cielab_dicom_to_hex(cielab_scaled(0, 0, 0)) == "#000000"
    assert cielab_dicom_to_hex(None) is None


# ------------------------------------------------------------------ detection

def test_detect_medical_format(tmp_path):
    nii = save_nifti(tmp_path / "a.nii", np.zeros((4, 4, 4), np.uint8))
    niigz = save_nifti(tmp_path / "a.nii.gz", np.zeros((4, 4, 4), np.uint8))
    assert detect_medical_format(nii, "Scan.NII") == ("segmentation", None)
    assert detect_medical_format(niigz, "labels.nii.gz") == ("segmentation", None)

    import nrrd

    nrrd_path = str(tmp_path / "a.nrrd")
    nrrd.write(nrrd_path, np.zeros((4, 4, 4), np.uint8))
    assert detect_medical_format(nrrd_path, "x.nrrd") == ("segmentation", None)
    assert detect_medical_format(nrrd_path, "x.seg.nrrd") == ("segmentation", None)

    kind, error = detect_medical_format(nii, "fake.nrrd")
    assert kind == "segmentation" and error  # content does not match the extension

    assert detect_medical_format(make_ct_zip(tmp_path), "series.zip") == ("dicom", None)
    mask_zip = tmp_path / "masks.zip"
    with zipfile.ZipFile(mask_zip, "w") as zf:
        zf.write(niigz, "liver.nii.gz")
    assert detect_medical_format(str(mask_zip), "masks.zip") == ("segmentation", None)

    plain = tmp_path / "model.stl"
    plain.write_bytes(b"solid x")
    assert detect_medical_format(str(plain), "model.stl") == (None, None)
    assert detect_medical_format(str(plain), "") == (None, None)


def test_detect_single_dcm_and_seg(tmp_path):
    folder = write_ct_series(tmp_path / "ct", n_slices=3)
    single = os.path.join(folder, sorted(os.listdir(folder))[0])
    kind, error = detect_medical_format(single, "slice.dcm")
    assert kind == "dicom"
    assert error == "Upload the whole DICOM series as a ZIP file (a single .dcm file holds only one slice)."
    broken = tmp_path / "broken.dcm"
    broken.write_bytes(b"\x00" * 10)
    assert detect_medical_format(str(broken), "broken.dcm")[1]


def test_detect_zip_problems(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../evil", "x")
        zf.writestr("a", "x")
        zf.writestr("b", "x")
    assert detect_medical_format(str(evil), "evil.zip")[1] == "This ZIP contains unsafe paths."

    absolute = tmp_path / "abs.zip"
    with zipfile.ZipFile(absolute, "w") as zf:
        zf.writestr("/etc/passwd", "x")
    assert detect_medical_format(str(absolute), "abs.zip")[1] == "This ZIP contains unsafe paths."

    garbage = tmp_path / "bad.zip"
    garbage.write_bytes(b"not a zip at all")
    assert detect_medical_format(str(garbage), "bad.zip")[1] == "This file is not a valid ZIP archive."

    small = tmp_path / "small.zip"
    with zipfile.ZipFile(small, "w") as zf:
        zf.writestr("one.txt", "x")
    kind, error = detect_medical_format(str(small), "small.zip")
    assert kind is None and error

    # dotfiles and __MACOSX do not count as slices
    macos = tmp_path / "mac.zip"
    with zipfile.ZipFile(macos, "w") as zf:
        zf.writestr("a", "x")
        zf.writestr("__MACOSX/b", "x")
        zf.writestr(".hidden", "x")
    assert detect_medical_format(str(macos), "mac.zip")[1]


def test_symlink_member_rejected(tmp_path):
    link = tmp_path / "link.zip"
    with zipfile.ZipFile(link, "w") as zf:
        info = zipfile.ZipInfo("series/slice0")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    assert detect_medical_format(str(link), "link.zip")[1] == "This ZIP contains unsafe paths."


def test_encrypted_zip_rejected(tmp_path):
    path = tmp_path / "locked.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(3):
            zf.writestr(f"s{i}", "x")
    raw = bytearray(path.read_bytes())
    # set the "encrypted" general-purpose flag in every local and central header
    for sig, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        pos = raw.find(sig)
        while pos != -1:
            raw[pos + offset] |= 1
            pos = raw.find(sig, pos + 1)
    path.write_bytes(bytes(raw))
    assert "Password-protected" in detect_medical_format(str(path), "locked.zip")[1]


# ------------------------------------------------------ privacy + child process

def test_patient_identifiers_never_leak(tmp_path, caplog, monkeypatch):
    zip_path = make_ct_zip(tmp_path)
    captured = []
    real_run = subprocess.run

    def spy(command, *args, **kwargs):
        proc = real_run(command, *args, **kwargs)
        with open(command[command.index("--result") + 1], encoding="utf-8") as fh:
            captured.append((proc.stdout, proc.stderr, fh.read()))
        return proc

    monkeypatch.setattr(subprocess, "run", spy)
    caplog.set_level(logging.DEBUG)
    converter = MedicalConverter("dicom", "bone")
    out = str(tmp_path / "out" / "model.glb")
    assert converter.convert(zip_path, out) is True
    assert converter.layers[0]["name"] == "Bone"

    # failing conversions too (errors must be generic)
    bad = MedicalConverter("dicom", "bone")
    assert bad.convert(make_ct_zip(tmp_path, name="ct2", skip=(5,)), str(tmp_path / "out" / "bad.glb")) is False

    # in-process path with the logger captured
    run_conversion("dicom", zip_path, str(tmp_path / "out" / "again.glb"), "bone")

    haystack = [
        caplog.text,
        json.dumps(converter.layers), json.dumps(converter.notes), json.dumps(converter.errors),
        json.dumps(bad.errors), json.dumps(converter.get_status()), json.dumps(bad.get_status()),
        *[part for triple in captured for part in triple],
    ]
    assert len(captured) == 2
    for text in haystack:
        for secret in (PATIENT_NAME, "Patient", PATIENT_ID, "19700101"):
            assert secret not in text
    with open(out, "rb") as fh:
        assert PATIENT_NAME.encode() not in fh.read()


def test_medical_converter_end_to_end_via_child_process(tmp_path):
    converter = MedicalConverter("dicom", "bone")
    out = str(tmp_path / "converted" / "model.glb")
    assert converter.convert(make_ct_zip(tmp_path), out, color="#ff0000", source_unit="mm", extra="ignored") is True
    assert os.path.getsize(out) > 0
    assert converter.errors == []
    assert converter.modality == "CT"
    assert converter.layers[0]["volume_ml"] == pytest.approx(SPHERE_ML, rel=0.15)
    assert [p for p in os.listdir(tmp_path / "converted") if p.startswith(".medical")] == []


def test_segmentation_end_to_end_via_child_process(tmp_path):
    path = save_nifti(tmp_path / "seg.nii.gz", label_volume((1, (32, 32, 32), 10)))
    converter = MedicalConverter("segmentation")
    assert converter.convert(path, str(tmp_path / "model.glb")) is True
    assert [l["name"] for l in converter.layers] == ["Label 1"]


def test_child_user_error_is_reported(tmp_path):
    converter = MedicalConverter("dicom", "bone")
    assert converter.convert(make_ct_zip(tmp_path, n_slices=2), str(tmp_path / "model.glb")) is False
    assert "At least 3 slices" in converter.errors[0]
    assert not os.path.exists(tmp_path / "model.glb")


def test_child_timeout_gives_friendly_error(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(subprocess, "run", boom)
    converter = MedicalConverter("dicom", "bone")
    assert converter.convert(make_ct_zip(tmp_path), str(tmp_path / "model.glb")) is False
    assert converter.errors == ["Processing this scan took too long. Try a smaller series or crop it."]


def test_child_crash_without_result_gives_friendly_error(tmp_path, monkeypatch, caplog):
    def crash(command, *args, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "Traceback (most recent call last):\nKilled")

    monkeypatch.setattr(subprocess, "run", crash)
    converter = MedicalConverter("dicom", "bone")
    assert converter.convert(make_ct_zip(tmp_path), str(tmp_path / "model.glb")) is False
    assert converter.errors == ["The scan could not be processed (out of memory or unreadable data)."]
    assert "Traceback" not in converter.errors[0]
    assert "Traceback" in caplog.text  # the stderr tail is kept in the log, not shown to users


def test_unknown_kind_and_preset_and_missing_file(tmp_path):
    assert MedicalConverter("stl").convert(str(tmp_path / "x"), str(tmp_path / "o.glb")) is False
    assert MedicalConverter("dicom", "nope").convert(str(tmp_path / "x"), str(tmp_path / "o.glb")) is False
    missing = MedicalConverter("segmentation")
    assert missing.convert(str(tmp_path / "missing.nii"), str(tmp_path / "o.glb")) is False
    assert missing.errors


# ------------------------------------------------------ ZIP of masks (images, label maps, tables)

def zip_nifti(tmp_path, members, extra=None, name="masks.zip"):
    """members: {archive name: array or (array, affine)}; extra: {archive name: text/bytes}."""
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        for member, data in members.items():
            array, affine = data if isinstance(data, tuple) else (data, None)
            zf.write(save_nifti(tmp_path / f"_{os.path.basename(member)}", array, affine), member)
        for member, content in (extra or {}).items():
            zf.writestr(member, content)
    return zip_path


def ct_image(shape=(24, 24, 24)):
    return np.random.default_rng(0).integers(-1000, 1500, shape).astype(np.int16)


def test_zip_skips_ct_image_next_to_masks(tmp_path):
    liver = sphere((32, 32, 32), (10, 16, 16), 6).astype(np.uint8)
    kidney = sphere((32, 32, 32), (22, 16, 16), 5).astype(np.uint8)
    # the CT sits on a different grid: it must not trigger the grid-mismatch error
    zip_path = zip_nifti(
        tmp_path,
        {"ct.nii.gz": ct_image(), "segmentations/liver.nii.gz": liver, "segmentations/kidney.nii.gz": kidney},
    )
    result, out = convert_in_process("segmentation", zip_path, tmp_path)
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Kidney", "Liver"]
    assert result["notes"] == ["Skipped ct.nii.gz: it looks like an image, not a mask."]
    assert sorted(m.name for m in pygltflib.GLTF2().load(out).materials) == ["Kidney", "Liver"]


@pytest.mark.parametrize(
    "image",
    [
        np.random.default_rng(1).normal(100, 30, (16, 16, 16)).astype(np.float32),  # non-integer floats
        np.random.default_rng(2).integers(0, 4000, (16, 16, 16)).astype(np.uint16),  # offset CT, many values
        np.random.default_rng(3).integers(-900, 0, (16, 16, 16)).astype(np.int16),  # negative HU
    ],
    ids=["float", "many-values", "negative"],
)
def test_zip_image_detection_and_only_images_error(tmp_path, image):
    mask = sphere((16, 16, 16), (8, 8, 8), 4).astype(np.uint8)
    ok, _ = convert_in_process(
        "segmentation", zip_nifti(tmp_path, {"scan.nii.gz": image, "liver.nii.gz": mask}), tmp_path / "a"
    )
    assert ok["ok"], ok["error"]
    assert [l["name"] for l in ok["layers"]] == ["Liver"]
    assert ok["notes"] == ["Skipped scan.nii.gz: it looks like an image, not a mask."]

    only, _ = convert_in_process(
        "segmentation", zip_nifti(tmp_path, {"a.nii.gz": image, "b.nii.gz": image}, name="img.zip"), tmp_path / "b"
    )
    assert not only["ok"] and only["error"] == "This ZIP contains no mask files (only images)."
    single, _ = convert_in_process(
        "segmentation", zip_nifti(tmp_path, {"a.nii.gz": image}, name="one.zip"), tmp_path / "c"
    )
    assert not single["ok"] and single["error"] == "This ZIP contains no mask files (only images)."


def test_zip_mask_grid_mismatch_still_rejected_among_masks(tmp_path):
    zip_path = zip_nifti(
        tmp_path,
        {"ct.nii.gz": ct_image(), "a.nii.gz": np.ones((8, 8, 8), np.uint8), "b.nii.gz": np.ones((9, 8, 8), np.uint8)},
    )
    result, _ = convert_in_process("segmentation", zip_path, tmp_path)
    assert not result["ok"] and "same size and orientation" in result["error"]


def test_zip_multilabel_map_expands_to_one_layer_per_label(tmp_path):
    multi = label_volume((1, (12, 16, 16), 6), (2, (24, 16, 16), 5), shape=(32, 32, 32))
    liver = sphere((32, 32, 32), (16, 6, 16), 3).astype(np.uint8)
    result, _ = convert_in_process(
        "segmentation", zip_nifti(tmp_path, {"liver.nii.gz": liver, "multi_seg.nii.gz": multi}), tmp_path
    )
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Liver", "Multi seg 1", "Multi seg 2"]
    volumes = {l["name"]: l["volume_ml"] for l in result["layers"]}
    assert volumes["Multi seg 1"] == pytest.approx(4 / 3 * math.pi * 6**3 / 1000, rel=0.2)
    assert volumes["Multi seg 2"] == pytest.approx(4 / 3 * math.pi * 5**3 / 1000, rel=0.25)


def test_zip_with_one_segmentation_behaves_like_direct_upload(tmp_path):
    multi = label_volume((1, (12, 16, 16), 6), (2, (24, 16, 16), 5), shape=(32, 32, 32))
    zipped, _ = convert_in_process("segmentation", zip_nifti(tmp_path, {"multi.nii.gz": multi}), tmp_path / "a")
    direct, _ = convert_in_process("segmentation", save_nifti(tmp_path / "multi.nii.gz", multi), tmp_path / "b")
    assert [l["name"] for l in zipped["layers"]] == [l["name"] for l in direct["layers"]] == ["Label 1", "Label 2"]

    import nrrd

    header = {"space": "right-anterior-superior", "space directions": np.eye(3), "space origin": np.zeros(3),
              "kinds": ["domain"] * 3, "Segment0_Name": "Left lung", "Segment0_LabelValue": "1", "Segment0_Layer": "0"}
    seg = str(tmp_path / "case.seg.nrrd")
    nrrd.write(seg, label_volume((1, (16, 16, 16), 6), shape=(32, 32, 32)), header)
    zip_path = tmp_path / "seg.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(seg, "case.seg.nrrd")
    nrrd_result, _ = convert_in_process("segmentation", zip_path, tmp_path / "c")
    assert [l["name"] for l in nrrd_result["layers"]] == ["Left lung"]


ITK_SNAP = """################################################
# ITK-SnAP Label Description File
################################################
    0     0    0    0        0  0  0    "Clear Label"
    1   255    0    0        1  1  1    "Liver"
"""
SLICER_CTBL = "# Color table file\n# 3 values\n1 Liver 255 0 0 255\n3 Spleen 0 255 0 255\n"
JSON_NAMES = json.dumps({"1": "Liver"})
JSON_COLORS = json.dumps({"1": {"name": "Liver", "color": "#ff0000"}})


@pytest.mark.parametrize(
    "filename,content,color",
    [
        ("labels.txt", ITK_SNAP, "#FF0000"),
        ("labels.ctbl", SLICER_CTBL, "#FF0000"),
        ("colors.txt", SLICER_CTBL, "#FF0000"),  # Slicer table under a .txt name: detected by content
        ("names.json", JSON_NAMES, None),
        ("names.json", JSON_COLORS, "#FF0000"),
    ],
)
def test_zip_label_table_names_and_colours(tmp_path, filename, content, color):
    multi = label_volume((1, (12, 16, 16), 6), (2, (24, 16, 16), 5), shape=(32, 32, 32))
    other = sphere((32, 32, 32), (16, 6, 16), 3).astype(np.uint8)
    # one-file ZIP and multi-file ZIP both apply the table; unknown labels keep their default name
    single, _ = convert_in_process(
        "segmentation", zip_nifti(tmp_path, {"m.nii.gz": multi}, {filename: content}, name="one.zip"), tmp_path / "a"
    )
    assert single["ok"], single["error"]
    assert [l["name"] for l in single["layers"]] == ["Liver", "Label 2"]
    if color:
        assert single["layers"][0]["color"] == color
    many, _ = convert_in_process(
        "segmentation",
        zip_nifti(tmp_path, {"m.nii.gz": multi, "other.nii.gz": other}, {filename: content}, name="two.zip"),
        tmp_path / "b",
    )
    assert [l["name"] for l in many["layers"]] == ["Liver", "M 2", "Other"]


def test_zip_garbage_label_table_is_ignored(tmp_path):
    multi = label_volume((1, (12, 16, 16), 6), (2, (24, 16, 16), 5), shape=(32, 32, 32))
    extra = {"readme.txt": "Segmentation exported on a Tuesday.\n1 2 3", "broken.json": "{not json", "list.json": "[1, 2]",
             "bad.ctbl": b"\xff\xfe\x00 garbage"}
    result, _ = convert_in_process(
        "segmentation", zip_nifti(tmp_path, {"multi.nii.gz": multi}, extra), tmp_path
    )
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Label 1", "Label 2"]


def test_nifti_header_extension_label_names(tmp_path):
    multi = label_volume((1, (12, 16, 16), 6), (2, (24, 16, 16), 5), shape=(32, 32, 32))

    def with_extension(path, content):
        img = nib.Nifti1Image(multi, np.eye(4))
        img.header.extensions.append(nib.nifti1.Nifti1Extension(6, content))
        nib.save(img, str(path))
        return str(path)

    named = with_extension(tmp_path / "named.nii.gz", json.dumps({"1": "liver", "2": "spleen"}).encode())
    result, _ = convert_in_process("segmentation", named, tmp_path / "a")
    assert [l["name"] for l in result["layers"]] == ["liver", "spleen"]
    zip_path = tmp_path / "ext.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(named, "named.nii.gz")
        zf.write(save_nifti(tmp_path / "_o.nii.gz", sphere((32, 32, 32), (16, 6, 16), 3).astype(np.uint8)), "o.nii.gz")
    in_zip, _ = convert_in_process("segmentation", zip_path, tmp_path / "b")
    assert [l["name"] for l in in_zip["layers"]] == ["liver", "spleen", "O"]
    # not a label table: ignored
    for junk in (b"not json", json.dumps({"patient": "x"}).encode(), json.dumps({"1": 5}).encode(), b""):
        path = with_extension(tmp_path / "junk.nii.gz", junk)
        result, _ = convert_in_process("segmentation", path, tmp_path / "c")
        assert [l["name"] for l in result["layers"]] == ["Label 1", "Label 2"]



# ------------------------------------------------------ DICOM zips: SEG objects, series, presets

SMALL_ML = 4 / 3 * math.pi * 7**3 / 1000


def test_dicom_seg_inside_ct_zip_replaces_threshold(tmp_path):
    folder = write_ct_series(tmp_path / "ct")
    shape = (64, 64, 40)
    liver = sphere(shape, (20, 20, 20), 8, (1, 1, 2))
    seg_dir = tmp_path / "seg"
    seg_dir.mkdir()
    write_dicom_seg(seg_dir / "seg.dcm", [liver], ["Liver"], [None], shape)
    zip_path = zip_folders([folder, seg_dir], tmp_path / "both.zip")
    result, _ = convert_in_process("dicom", zip_path, tmp_path, "bone")
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Liver"]
    assert result["notes"] == ["Used the DICOM segmentation found in the ZIP instead of a threshold."]


def test_several_dicom_segs_use_the_one_with_most_frames(tmp_path):
    folder = write_ct_series(tmp_path / "ct")
    shape = (64, 64, 40)
    seg_dir = tmp_path / "seg"
    seg_dir.mkdir()
    write_dicom_seg(seg_dir / "small.dcm", [sphere(shape, (20, 20, 20), 3, (1, 1, 2))], ["Small"], [None], shape)
    write_dicom_seg(seg_dir / "big.dcm", [sphere(shape, (30, 30, 30), 10, (1, 1, 2))], ["Big"], [None], shape)
    result, _ = convert_in_process("dicom", zip_folders([folder, seg_dir], tmp_path / "x.zip"), tmp_path, "bone")
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Big"]
    assert "Used the DICOM segmentation found in the ZIP instead of a threshold." in result["notes"]
    assert any("2 DICOM segmentations" in n and "most frames" in n for n in result["notes"])


def test_unreadable_dicom_seg_falls_back_to_threshold(tmp_path):
    folder = write_ct_series(tmp_path / "ct")
    seg_dir = tmp_path / "seg"
    seg_dir.mkdir()
    shape = (64, 64, 40)
    seg = write_dicom_seg(seg_dir / "seg.dcm", [sphere(shape, (20, 20, 20), 5, (1, 1, 2))], ["Liver"], [None], shape)
    import pydicom

    ds = pydicom.dcmread(seg)
    del ds.PerFrameFunctionalGroupsSequence
    ds.save_as(seg)
    result, _ = convert_in_process("dicom", zip_folders([folder, seg_dir], tmp_path / "x.zip"), tmp_path, "bone")
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Bone"]
    assert any("segmentation" in n and "could not be used" in n for n in result["notes"])


def test_several_image_series_note_counts_only(tmp_path):
    big = write_ct_series(tmp_path / "big", n_slices=40)
    small = write_ct_series(tmp_path / "small", n_slices=20)
    result, _ = convert_in_process("dicom", zip_folders([big, small], tmp_path / "two.zip"), tmp_path, "bone")
    assert result["ok"], result["error"]
    assert result["notes"] == ["This ZIP has 2 image series; used the one with 40 slices."]
    single, _ = convert_in_process("dicom", make_ct_zip(tmp_path, name="one"), tmp_path / "b", "bone")
    assert single["notes"] == []


def test_skin_preset_keeps_only_the_body(tmp_path):
    # a large body plus a separate dense "table" blob that is no part of it
    spheres = [(32.0, 32.0, 40.0, 15.0), (52.0, 52.0, 16.0, 8.0)]
    zip_path = make_ct_zip(tmp_path, spheres=spheres)
    bone, _ = convert_in_process("dicom", zip_path, tmp_path / "a", "bone")
    skin, _ = convert_in_process("dicom", zip_path, tmp_path / "b", "skin")
    assert bone["ok"] and skin["ok"], skin["error"]
    assert bone["layers"][0]["volume_ml"] == pytest.approx(SPHERE_ML + 4 / 3 * math.pi * 8**3 / 1000, rel=0.1)
    assert skin["layers"][0]["volume_ml"] == pytest.approx(SPHERE_ML, rel=0.1)
    assert "table" not in MEDICAL_PRESETS["skin"]["description"]


def test_multiple_presets_make_one_layer_each_without_overlap(tmp_path):
    # a bright "bone" sphere and a separate contrast-level sphere (200 HU) on one volume
    import pydicom

    folder = write_ct_series(tmp_path / "ct", spheres=[(20.0, 32.0, 40.0, 10.0)])
    for name in sorted(os.listdir(folder)):  # paint the second sphere at 200 HU into every slice
        ds = pydicom.dcmread(os.path.join(folder, name))
        k = int(name[6:9])
        arr = np.frombuffer(ds.PixelData, dtype="<u2").reshape(64, 64).copy()
        rr, cc = np.meshgrid(np.arange(64), np.arange(64), indexing="ij")
        inside = (cc - 48) ** 2 + (rr - 32) ** 2 + (2 * k - 40) ** 2 <= 7**2
        arr[inside] = 200 + 1024
        ds.PixelData = arr.tobytes()
        ds.save_as(os.path.join(folder, name))
    zip_path = zip_folder(folder, tmp_path / "two.zip")
    result, out = convert_in_process("dicom", zip_path, tmp_path / "a", "bone,contrast")
    assert result["ok"], result["error"]
    volumes = {l["name"]: l for l in result["layers"]}
    assert list(volumes) == ["Bone", "Contrast vessels"]
    assert volumes["Bone"]["color"] == "#E8D5B7" and volumes["Contrast vessels"]["color"] == "#CC2222"
    assert volumes["Bone"]["volume_ml"] == pytest.approx(4 / 3 * math.pi * 10**3 / 1000, rel=0.15)
    assert volumes["Contrast vessels"]["volume_ml"] == pytest.approx(SMALL_ML, rel=0.15)  # bone is left out
    assert sorted(layer_meshes(out)) == ["Bone", "Contrast vessels"]
    alone, _ = convert_in_process("dicom", zip_path, tmp_path / "b", "contrast")
    assert alone["layers"][0]["volume_ml"] > SMALL_ML * 2  # without bone the contrast layer includes it

    both, _ = convert_in_process("dicom", zip_path, tmp_path / "c", "bone,skin,bone")
    assert [l["name"] for l in both["layers"]] == ["Bone", "Skin"]  # duplicates removed
    assert "do not overlap" in MEDICAL_PRESETS["contrast"]["description"]


def test_new_paths_never_leak_patient_identifiers(tmp_path, caplog):
    import pydicom

    caplog.set_level(logging.DEBUG)
    shape = (64, 64, 40)
    ct = write_ct_series(tmp_path / "ct", n_slices=40)
    other = write_ct_series(tmp_path / "other", n_slices=20)
    good, bad = tmp_path / "good", tmp_path / "bad"
    for folder in (good, bad):
        folder.mkdir()
        write_dicom_seg(folder / "seg.dcm", [sphere(shape, (20, 20, 20), 8, (1, 1, 2))], ["Liver"], [None], shape)
    ds = pydicom.dcmread(str(bad / "seg.dcm"))
    del ds.PerFrameFunctionalGroupsSequence  # unusable SEG: falls back to a threshold with a note
    ds.save_as(str(bad / "seg.dcm"))
    runs = [  # several series + several presets, SEG used, SEG unusable
        (zip_folders([ct, other], tmp_path / "a.zip"), "bone,skin,contrast"),
        (zip_folders([ct, good], tmp_path / "b.zip"), "bone"),
        (zip_folders([ct, bad], tmp_path / "c.zip"), "bone"),
    ]
    texts = []
    for i, (zip_path, preset) in enumerate(runs):
        converter = MedicalConverter("dicom", preset)
        assert converter.convert(zip_path, str(tmp_path / f"m{i}.glb")) is True
        texts += [json.dumps(converter.layers), json.dumps(converter.notes), json.dumps(converter.errors)]
        with open(tmp_path / f"m{i}.glb", "rb") as fh:
            texts.append(fh.read().decode("latin-1"))
    in_process, _ = convert_in_process("dicom", runs[2][0], tmp_path / "x", "bone")
    texts += [json.dumps(in_process), caplog.text]
    assert len(texts) == 14 and any("could not be used" in t for t in texts)
    for text in texts:
        for secret in (PATIENT_NAME, "Patient", PATIENT_ID, "19700101"):
            assert secret not in text


def test_multiple_presets_on_mr_fall_back_to_one_auto_layer(tmp_path):
    zip_path = make_ct_zip(tmp_path, modality="MR", background=0, fg=800)
    result, _ = convert_in_process("dicom", zip_path, tmp_path, "bone,skin")
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Auto threshold"]
    assert len([n for n in result["notes"] if "not CT" in n]) == 1


def test_unknown_preset_in_list_rejected(tmp_path):
    result, _ = convert_in_process("dicom", make_ct_zip(tmp_path), tmp_path, "bone,nope")
    assert not result["ok"] and result["error"] == "Unknown preset."
    converter = MedicalConverter("dicom", "bone,nope")
    assert converter.convert(str(tmp_path / "ct.zip"), str(tmp_path / "o.glb")) is False
    assert converter.errors == ["Unknown preset."]


def test_multiple_presets_via_child_process(tmp_path):
    converter = MedicalConverter("dicom", "bone,skin")
    assert converter.convert(make_ct_zip(tmp_path), str(tmp_path / "model.glb")) is True
    assert [l["name"] for l in converter.layers] == ["Bone", "Skin"]


# ------------------------------------------------------ thin structures

def test_thin_structure_survives_weaker_smoothing(tmp_path):
    vol = label_volume((1, (20, 32, 32), 10), shape=(64, 64, 64))
    vol[30:50, 40, 40] = 2  # a one-voxel-wide rod, thinner than the default smoothing kernel
    result, out = convert_in_process("segmentation", save_nifti(tmp_path / "thin.nii.gz", vol), tmp_path)
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Label 1", "Label 2"]
    assert len(layer_meshes(out)["Label 2"].faces) > 0
    assert not result["notes"]


def test_structure_lost_to_downsampling_is_reported(tmp_path, monkeypatch):
    vol = label_volume((1, (32, 32, 32), 14), shape=(64, 64, 64))
    vol[5, 5, 5] = 2  # an odd index: a stride of 2 steps over it
    monkeypatch.setenv("MEDICAL_MAX_VOXELS", "40000")
    result, _ = convert_in_process("segmentation", save_nifti(tmp_path / "t.nii.gz", vol), tmp_path)
    assert result["ok"], result["error"]
    assert [l["name"] for l in result["layers"]] == ["Label 1"]
    assert "Label 2 is too thin to display at this resolution." in result["notes"]


def test_structure_that_cannot_be_meshed_is_reported(tmp_path, monkeypatch):
    from skimage import measure

    real = measure.marching_cubes

    def picky(volume, level=None, **kwargs):
        if volume.max() <= 1.0 and volume.sum() < 5:  # reject the tiny structure at every smoothing level
            raise ValueError("no surface")
        return real(volume, level=level, **kwargs)

    monkeypatch.setattr(measure, "marching_cubes", picky)
    vol = label_volume((1, (32, 32, 32), 10), shape=(64, 64, 64))
    vol[10, 10, 10] = 2
    result, _ = convert_in_process("segmentation", save_nifti(tmp_path / "t.nii.gz", vol), tmp_path)
    assert result["ok"], result["error"]
    assert "Label 2 is too thin to display at this resolution." in result["notes"]


# ------------------------------------------------------ upload-time ZIP sanity check

def test_detect_rejects_zip_of_non_dicom_files(tmp_path):
    jpegs = tmp_path / "photos.zip"
    with zipfile.ZipFile(jpegs, "w") as zf:
        for i in range(4):
            zf.writestr(f"p{i}.jpg", b"\xff\xd8\xff\xe0" + os.urandom(2000))
    kind, error = detect_medical_format(str(jpegs), "photos.zip")
    assert kind is None and error.startswith("This ZIP does not look like a DICOM series or a segmentation")


def test_detect_accepts_dicom_zip_without_preamble_or_with_junk_first(tmp_path):
    folder = write_ct_series(tmp_path / "ct", n_slices=6)
    bare = tmp_path / "bare"
    bare.mkdir()
    for name in sorted(os.listdir(folder)):  # drop the 128-byte preamble and the DICM marker
        with open(os.path.join(folder, name), "rb") as src, open(bare / name, "wb") as dst:
            dst.write(src.read()[132:])
    assert detect_medical_format(zip_folder(bare, tmp_path / "bare.zip"), "bare.zip") == ("dicom", None)
    with_junk = zip_folder(folder, tmp_path / "junk.zip", extra={"README.txt": "hi", "autorun.inf": "x", "a.exe": "MZ"})
    assert detect_medical_format(with_junk, "junk.zip") == ("dicom", None)
