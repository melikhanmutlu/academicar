"""Department / unit suggestions cover the medical departments (all 44
departments of the Istanbul University-Cerrahpaşa Faculty of Medicine)."""
import app as app_module
from tests.conftest import register


def test_medical_departments_are_suggested_on_new_project_form(client):
    register(client)
    html = client.get("/projects/new").data.decode()
    for name in ("Pediatrics", "Neurosurgery", "Ophthalmology", "Anatomy", "Psychiatry", "Plastic Surgery"):
        assert f'<option value="{name}">' in html
    assert len(app_module.MEDICAL_DEPARTMENTS) == 44


def test_official_long_names_group_with_existing_canonical_names():
    norm = app_module.normalize_department
    assert norm("Orthopaedics and Traumatology") == "Orthopaedics"
    assert norm("plastic, reconstructive and aesthetic surgery") == "Plastic Surgery"
    assert norm("Paediatrics") == "Pediatrics"
    assert norm("Chest Diseases") == "Pulmonary Medicine"
    # Free text is still accepted as typed.
    assert norm("  Veterinary   Anatomy ") == "Veterinary Anatomy"


def test_every_alias_points_at_a_suggested_department():
    names = set(app_module.INSTITUTION_DEPARTMENTS)
    assert all(target in names for target in app_module._DEPARTMENT_ALIASES.values())
