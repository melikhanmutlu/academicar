"""Faz 3 — Ölçü & Annotation tests.

Validates:
- ModelAnnotation model and to_dict
- human_scale_reference helper
- Annotation hotspots in viewer template
- Annotation management UI in model_edit template
- Dimension display in viewer template
- Migration exists
"""

from pathlib import Path

import pytest

# Import the helper directly from the app module
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


class TestModelAnnotation:
    """ModelAnnotation table and methods."""

    def test_class_exists(self):
        from models import ModelAnnotation
        assert hasattr(ModelAnnotation, 'model_id')
        assert hasattr(ModelAnnotation, 'position_x')
        assert hasattr(ModelAnnotation, 'label')
        assert hasattr(ModelAnnotation, 'description')
        assert hasattr(ModelAnnotation, 'order_index')

    def test_to_dict_method(self):
        from models import ModelAnnotation
        ann = ModelAnnotation(
            id=1,
            model_id="test-uuid",
            position_x=1.0, position_y=2.0, position_z=3.0,
            normal_x=0.0, normal_y=1.0, normal_z=0.0,
            label="Test label",
            description="Test desc",
            order_index=0,
        )
        d = ann.to_dict()
        assert d["label"] == "Test label"
        assert d["position"] == [1.0, 2.0, 3.0]
        assert d["normal"] == [0.0, 1.0, 0.0]
        assert d["description"] == "Test desc"

    def test_model_relationship(self):
        from models import Model3D
        assert hasattr(Model3D, 'annotations')


class TestHumanScaleReference:
    """human_scale_reference helper."""

    def _import_helper(self):
        # We need to import from app module
        from app import human_scale_reference
        return human_scale_reference

    def test_credit_card_range(self):
        ref = self._import_helper()
        result = ref("8.5 x 5.4 x 0.1 cm")
        assert result is not None
        assert "credit card" in result

    def test_hand_span_range(self):
        ref = self._import_helper()
        result = ref("14.0 x 10.0 x 8.0 cm")
        assert result is not None
        assert "hand" in result.lower() or "cm" in result

    def test_none_for_missing(self):
        ref = self._import_helper()
        assert ref(None) is None
        assert ref("") is None

    def test_none_for_invalid(self):
        ref = self._import_helper()
        assert ref("Not measured") is None

    def test_ruler_range(self):
        ref = self._import_helper()
        result = ref("30.0 x 5.0 x 2.0 cm")
        assert result is not None
        assert "ruler" in result

    def test_coin_range(self):
        ref = self._import_helper()
        result = ref("2.5 x 2.5 x 0.2 cm")
        assert result is not None
        assert "coin" in result


class TestViewerAnnotationHotspots:
    """Viewer template renders annotation hotspots."""

    def test_has_annotation_hotspot_class(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "annotation-hotspot" in content

    def test_has_hotspot_slot(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "hotspot-ann-" in content

    def test_has_annotations_toggle(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "annotationsBtn" in content

    def test_has_dimension_display(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "dimensions_cm" in content
        assert "scale_reference" in content


class TestViewerInteractiveAnnotation:
    """Viewer has interactive annotation placement for owners."""

    def test_has_annotate_button(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "annotateBtn" in content

    def test_has_annotate_form(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "annotateForm" in content
        assert "annLabelInput" in content
        assert "annDescInput" in content

    def test_has_position_from_point_api(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "positionAndNormalFromPoint" in content

    def test_owner_only_guard(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "is_owner" in content

    def test_delete_button_on_hotspots(self):
        content = (TEMPLATES_DIR / "viewer.html").read_text()
        assert "annotation-delete-btn" in content
        assert "data-delete-ann" in content


class TestModelEditAnnotations:
    """model_edit.html links to viewer for annotation placement."""

    def test_has_annotation_section(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        assert "annotationSection" in content

    def test_links_to_viewer_for_annotation(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        assert "Open Viewer to Annotate" in content

    def test_has_delete_annotation(self):
        content = (TEMPLATES_DIR / "model_edit.html").read_text()
        assert "deleteAnnotation" in content


class TestMigrationExists:
    """Migration for model_annotations table."""

    def test_migration_file_exists(self):
        migration = Path(__file__).resolve().parent.parent / "migrations" / "versions" / "c3d4e5f6g7h8_create_model_annotations.py"
        assert migration.exists()
        content = migration.read_text()
        assert "model_annotations" in content
        assert "position_x" in content
        assert "label" in content
