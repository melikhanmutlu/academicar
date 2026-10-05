"""Synthetic medical data builders for tests/test_medical_converter.py (nothing is stored on disk)."""

import os
import zipfile

import numpy as np
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import (
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    JPEG2000Lossless,
    generate_uid,
)

CT_STORAGE = "1.2.840.10008.5.1.4.1.1.2"
MR_STORAGE = "1.2.840.10008.5.1.4.1.1.4"
SEG_STORAGE = "1.2.840.10008.5.1.4.1.1.66.4"

PATIENT_NAME = "Test^Patient"
PATIENT_ID = "PID-424242"


def sphere(shape, centre, radius, spacing=(1.0, 1.0, 1.0)):
    """Boolean sphere on an index grid; centre/radius in mm, shape = (x, y, z) voxels."""
    grids = np.meshgrid(*[np.arange(n) * s for n, s in zip(shape, spacing)], indexing="ij")
    dist2 = sum((g - c) ** 2 for g, c in zip(grids, centre))
    return dist2 <= radius**2


def write_ct_series(
    folder,
    spheres=((32.0, 32.0, 40.0, 15.0),),
    n_slices=40,
    modality="CT",
    skip=(),
    implicit=False,
    compressed=False,
    background=-1000,
    fg=1000,
):
    """Write a 64x64 series, 1 mm pixels, 2 mm slices; spheres are (x, y, z, radius) in mm.

    Pixel (row r, col c) of slice k sits at x=c, y=r, z=2k millimetres (LPS).
    """
    os.makedirs(folder, exist_ok=True)
    shape = (64, 64, n_slices)  # (x=col, y=row, z=slice)
    hu = np.full(shape, background, dtype=np.int32)
    for cx, cy, cz, r in spheres:
        hu[sphere(shape, (cx, cy, cz), r, (1.0, 1.0, 2.0))] = fg
    study, series = generate_uid(), generate_uid()
    intercept = -1024 if modality == "CT" else 0
    for k in range(n_slices):
        if k in skip:
            continue
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = CT_STORAGE if modality == "CT" else MR_STORAGE
        sop = generate_uid()
        meta.MediaStorageSOPInstanceUID = sop
        meta.TransferSyntaxUID = (
            JPEG2000Lossless if compressed else (ImplicitVRLittleEndian if implicit else ExplicitVRLittleEndian)
        )
        ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID = meta.MediaStorageSOPClassUID
        ds.SOPInstanceUID = sop
        ds.PatientName = PATIENT_NAME
        ds.PatientID = PATIENT_ID
        ds.PatientBirthDate = "19700101"
        ds.StudyInstanceUID = study
        ds.SeriesInstanceUID = series
        ds.Modality = modality
        ds.InstanceNumber = k + 1
        ds.Rows = ds.Columns = 64
        ds.PixelSpacing = [1, 1]
        ds.SliceThickness = 2
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [0, 0, 2 * k]
        ds.RescaleSlope = 1
        ds.RescaleIntercept = intercept
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 0
        # array is (x, y); DICOM rows run along y, so transpose to (row, col).
        stored = (hu[:, :, k].T - intercept).astype("<u2")
        if compressed:
            from pydicom.encaps import encapsulate

            ds.PixelData = encapsulate([stored.tobytes()])
            ds["PixelData"].is_undefined_length = True
        else:
            ds.PixelData = stored.tobytes()
        ds.save_as(os.path.join(folder, f"slice_{k:03d}.dcm"))
    return folder


def zip_folder(folder, zip_path, extra=None):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(folder)):
            zf.write(os.path.join(folder, name), f"series/{name}")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return str(zip_path)


def zip_folders(folders, zip_path, extra=None):
    """ZIP several folders side by side (e.g. two series, or a series plus a DICOM-SEG)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder in folders:
            base = os.path.basename(str(folder))
            for name in sorted(os.listdir(folder)):
                zf.write(os.path.join(folder, name), f"{base}/{name}")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return str(zip_path)


def cielab_scaled(l_star, a_star, b_star):
    """L*a*b* -> DICOM 16-bit scaled RecommendedDisplayCIELabValue."""
    return [
        round(l_star / 100 * 65535),
        round((a_star + 128) / 255 * 65535),
        round((b_star + 128) / 255 * 65535),
    ]


def write_dicom_seg(path, masks, labels, colors, shape=(64, 64, 40)):
    """Write a BINARY DICOM-SEG (1-bit packed) with per-frame functional groups.

    ``masks`` are boolean (x, y, z) arrays on the same grid as write_ct_series;
    only slices where a segment has voxels become frames (as real writers do).
    """
    from pydicom.pixels import pack_bits

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SEG_STORAGE
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = SEG_STORAGE
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.PatientName = PATIENT_NAME
    ds.PatientID = PATIENT_ID
    ds.Modality = "SEG"
    ds.SeriesInstanceUID = generate_uid()
    ds.SegmentationType = "BINARY"
    ds.Rows, ds.Columns = shape[1], shape[0]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = ds.BitsStored = 1
    ds.HighBit = 0
    ds.PixelRepresentation = 0

    segments = Sequence()
    for number, (label, color) in enumerate(zip(labels, colors), start=1):
        seg = Dataset()
        seg.SegmentNumber = number
        seg.SegmentLabel = label
        seg.SegmentAlgorithmType = "MANUAL"
        if color is not None:
            seg.RecommendedDisplayCIELabValue = color
        segments.append(seg)
    ds.SegmentSequence = segments

    shared = Dataset()
    orient = Dataset()
    orient.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    shared.PlaneOrientationSequence = Sequence([orient])
    measures = Dataset()
    measures.PixelSpacing = [1, 1]
    measures.SliceThickness = 2
    shared.PixelMeasuresSequence = Sequence([measures])
    ds.SharedFunctionalGroupsSequence = Sequence([shared])

    frames, per_frame = [], Sequence()
    for number, mask in enumerate(masks, start=1):
        for k in range(mask.shape[2]):
            if not mask[:, :, k].any():
                continue
            frames.append(mask[:, :, k].T.astype(np.uint8))  # (row, col)
            item = Dataset()
            ident = Dataset()
            ident.ReferencedSegmentNumber = number
            item.SegmentIdentificationSequence = Sequence([ident])
            plane = Dataset()
            plane.ImagePositionPatient = [0, 0, 2 * k]
            item.PlanePositionSequence = Sequence([plane])
            per_frame.append(item)
    ds.PerFrameFunctionalGroupsSequence = per_frame
    ds.NumberOfFrames = len(frames)
    ds.PixelData = pack_bits(np.stack(frames), pad=True)
    ds.save_as(path)
    return str(path)
