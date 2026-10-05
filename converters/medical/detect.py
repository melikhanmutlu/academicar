"""Cheap upload-time classification of medical files (header/central-directory only)."""

from __future__ import annotations

from .common import MedicalError, inspect_zip, is_segmentation_name

SINGLE_SLICE_MESSAGE = "Upload the whole DICOM series as a ZIP file (a single .dcm file holds only one slice)."
_ZIP_UNRECOGNISED = (
    "This ZIP does not look like a DICOM series or a segmentation "
    "(expected DICOM slices, or .nii / .nii.gz / .nrrd mask files)."
)


def _magic_ok(path: str, name: str) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(352)
    except OSError:
        return False
    if name.endswith(".nii.gz"):
        return head[:2] == b"\x1f\x8b"
    if name.endswith(".nii"):
        return head[344:348] in (b"n+1\0", b"ni1\0") or head[4:8] == b"n+2\0"
    return head[:4] == b"NRRD"


def detect_medical_format(path: str, original_name: str):
    """Classify an upload without decoding pixels or extracting anything.

    Returns ``(kind, error)``:
      * ``("segmentation", None)`` - NIfTI / NRRD / DICOM-SEG, or a ZIP holding NIfTI/NRRD masks
      * ``("dicom", None)`` - a ZIP that looks like a DICOM series
      * ``(kind, message)`` - medical but unusable; ``message`` is user-facing and
        callers must check it before ``kind``. A lone non-SEG ``.dcm`` is reported
        as ``("dicom", SINGLE_SLICE_MESSAGE)``.
      * ``(None, None)`` - not a medical upload at all
    """
    name = (original_name or "").lower()
    if name.endswith((".nii", ".nii.gz", ".nrrd")):
        if not _magic_ok(path, name):
            return "segmentation", "This file is not a valid NIfTI/NRRD segmentation."
        return "segmentation", None
    if name.endswith(".dcm"):
        import pydicom

        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True, force=True)
            modality = str(getattr(ds, "Modality", "") or "")
        except Exception:
            return "dicom", "This DICOM file could not be read."
        if modality == "SEG":
            return "segmentation", None
        return "dicom", SINGLE_SLICE_MESSAGE
    if name.endswith(".zip"):
        try:
            members = inspect_zip(path)
        except MedicalError as exc:
            return None, str(exc)
        if any(is_segmentation_name(m.filename) for m in members):
            return "segmentation", None
        if len(members) >= 3:
            return "dicom", None
        return None, _ZIP_UNRECOGNISED
    return None, None
