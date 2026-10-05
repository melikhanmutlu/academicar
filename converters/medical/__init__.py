"""Medical imaging inputs (DICOM series, segmentations) -> layered GLB.

Heavy numeric dependencies are imported lazily inside the submodules, so
importing this package (e.g. for ``detect_medical_format`` in a web request)
stays cheap.
"""

from .common import MAX_LAYERS, MEDICAL_PRESETS
from .converter import MedicalConverter
from .detect import detect_medical_format

__all__ = ["MEDICAL_PRESETS", "MAX_LAYERS", "MedicalConverter", "detect_medical_format"]
