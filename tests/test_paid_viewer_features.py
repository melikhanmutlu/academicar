"""Viewer capability split: Free has everything except the paid-only tools."""
import importlib.util
import os

from licensing import PAID_ONLY_FEATURES, PLAN_FEATURES, default_license_plans

V39 = {"scenes", "scene_ar", "section_plane", "layer_metrics", "figure_export", "comparison"}
OPEN_TO_ALL = {"scenes", "scene_ar", "section_plane", "slice_view", "custom_threshold"}


def _migration(name):
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "versions", name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_feature_split_between_free_and_paid_plans():
    assert PAID_ONLY_FEATURES == {"layer_metrics", "figure_export", "comparison"}
    assert (V39 | OPEN_TO_ALL) <= {key for key, _ in PLAN_FEATURES}
    plans = default_license_plans()
    assert not plans["free"].features & PAID_ONLY_FEATURES
    assert OPEN_TO_ALL | {"ar", "annotations", "screenshots"} <= plans["free"].features
    for key in ("academic", "extended_archive", "institutional"):
        assert V39 | OPEN_TO_ALL <= plans[key].features, key


def test_backfill_migrations_match_the_python_defaults():
    v39 = _migration("b6c7d8e9f0a1_paid_viewer_features.py")
    assert set(v39.NEW_FEATURES) == V39
    assert set(v39.PAID_KEYS) == {"academic", "extended_archive", "institutional"}
    opened = _migration("c7d8e9f0a1b2_open_viewer_tools_to_all_plans.py")
    assert set(opened.NEW_FEATURES) | set(opened.OPENED_TO_FREE) == OPEN_TO_ALL
    assert V39 - set(opened.OPENED_TO_FREE) == PAID_ONLY_FEATURES
    assert set(opened.ALL_KEYS) == set(default_license_plans())
