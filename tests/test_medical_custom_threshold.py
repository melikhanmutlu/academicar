"""Custom CT threshold (HU range) for DICOM uploads."""
import pytest
from werkzeug.datastructures import MultiDict

from converters.medical.common import MedicalError, parse_custom_preset, parse_presets, preset_info
from models import Model3D, db
from tests.test_medical_converter import SPHERE_ML, convert_in_process, make_ct_zip
from tests.test_medical_upload_pipeline import _ct_zip, _project, _upload


def test_custom_keys_parse_and_validate():
    assert parse_custom_preset("custom:300") == (300, None)
    assert parse_custom_preset("custom:-100:80") == (-100, 80)
    for bad in ("custom", "custom:abc", "custom:300:200", "custom:-2000", "custom:0:5000", "bone"):
        assert parse_custom_preset(bad) is None, bad
    assert parse_presets("bone,custom:300:900") == ["bone", "custom:300:900"]
    with pytest.raises(MedicalError):
        parse_presets("custom:9999")
    assert preset_info("custom:300:900")["label"] == "Custom (300 to 900 HU)"


def test_custom_range_keeps_only_voxels_inside_it(tmp_path):
    zip_path = make_ct_zip(tmp_path, fg=200)  # a 200 HU sphere
    inside, _ = convert_in_process("dicom", zip_path, tmp_path / "a", "custom:100:300")
    assert inside["ok"], inside["error"]
    assert inside["layers"][0]["name"] == "Custom (100 to 300 HU)"
    assert inside["layers"][0]["volume_ml"] == pytest.approx(SPHERE_ML, rel=0.15)
    above, _ = convert_in_process("dicom", zip_path, tmp_path / "b", "custom:250:900")
    assert not above["ok"]  # the sphere is below the range


def test_custom_threshold_on_mr_falls_back_to_auto(tmp_path):
    zip_path = make_ct_zip(tmp_path, modality="MR", background=0, fg=800)
    result, _ = convert_in_process("dicom", zip_path, tmp_path, "custom:100")
    assert result["ok"], result["error"]
    assert result["layers"][0]["name"] == "Auto threshold"


def test_form_values_become_one_custom_key():
    from app import medical_presets_from_form, normalize_medical_presets

    form = MultiDict([("medical_preset", "custom"), ("medical_preset", "bone"), ("medical_hu_min", "300"), ("medical_hu_max", "900")])
    assert normalize_medical_presets(medical_presets_from_form(form)) == "bone,custom:300:900"
    form = MultiDict([("medical_preset", "custom"), ("medical_hu_min", "300"), ("medical_hu_max", "")])
    assert normalize_medical_presets(medical_presets_from_form(form)) == "custom:300"
    form = MultiDict([("medical_preset", "custom"), ("medical_hu_min", "900"), ("medical_hu_max", "300")])
    assert medical_presets_from_form(form) == ["custom"]
    assert normalize_medical_presets(["custom:1", "custom:2"]) is None


def test_upload_with_custom_threshold(client, tmp_path):
    slug = _project(client)
    _upload(client, slug, _ct_zip(tmp_path), "ct.zip", medical_preset=["bone", "custom"], medical_hu_min="500")
    with client.application.app_context():
        model = Model3D.query.one()
        assert model.processing_status == "ready", model.processing_error
        assert [layer["name"] for layer in model.layer_info["layers"]] == ["Bone", "Custom (500 HU and above)"]


def test_invalid_custom_range_is_explained(client, tmp_path):
    slug = _project(client)
    html = _upload(client, slug, _ct_zip(tmp_path), "ct.zip", medical_preset="custom", medical_hu_min="x").get_data(as_text=True)
    assert "Enter a custom threshold" in html
    with client.application.app_context():
        assert Model3D.query.count() == 0


def test_plan_without_custom_threshold_rejects_it(client, tmp_path):
    from licensing import get_license_plan, refresh_license_plan_cache
    from models import LicensePlanConfig

    slug = _project(client)
    with client.application.app_context():
        plan = get_license_plan("free")
        db.session.add(LicensePlanConfig(
            key="free", label=plan.label, price_usd_cents=0, duration_days=plan.duration_days,
            storage_limit_bytes=plan.storage_limit_bytes, is_purchasable=True,
            features=sorted(plan.features - {"custom_threshold"}), max_models_per_project=1,
        ))
        db.session.commit()
        refresh_license_plan_cache()
    html = _upload(client, slug, _ct_zip(tmp_path), "ct.zip", medical_preset="custom", medical_hu_min="500").get_data(as_text=True)
    assert "not included in the" in html
    with client.application.app_context():
        assert Model3D.query.count() == 0
