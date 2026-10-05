"""v39 viewer capabilities are paid-only: Free keeps the older features but none of these."""
import importlib.util
import os

from licensing import PAID_ONLY_FEATURES, PLAN_FEATURES, default_license_plans

NEW = {"scenes", "scene_ar", "section_plane", "layer_metrics", "figure_export", "comparison"}


def test_new_features_are_declared_and_paid_only():
    assert PAID_ONLY_FEATURES == NEW
    assert NEW <= {key for key, _ in PLAN_FEATURES}
    plans = default_license_plans()
    assert not plans["free"].features & NEW
    assert {"ar", "annotations", "screenshots"} <= plans["free"].features
    for key in ("academic", "extended_archive", "institutional"):
        assert NEW <= plans[key].features, key


def test_backfill_migration_matches_the_python_defaults():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "versions", "b6c7d8e9f0a1_paid_viewer_features.py")
    spec = importlib.util.spec_from_file_location("paid_features_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module.NEW_FEATURES) == PAID_ONLY_FEATURES
    assert set(module.PAID_KEYS) == {"academic", "extended_archive", "institutional"}
