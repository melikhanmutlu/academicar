"""Picked colours are sRGB; glTF baseColorFactor is linear. Whole-model colours must convert."""
import pytest

from converters.stl_converter import _srgb_to_linear


def test_hex_to_rgba_returns_linear_factors():
    from app import hex_to_rgba

    r, g, b, a = hex_to_rgba("#E8D5B7")
    assert (r, g, b) == pytest.approx(tuple(_srgb_to_linear(c / 255) for c in (0xE8, 0xD5, 0xB7)))
    assert a == 1.0
    assert hex_to_rgba("#808080")[0] == pytest.approx(0.2158605, abs=1e-6)  # not 0.502
    assert hex_to_rgba("#ffffff") == (1.0, 1.0, 1.0, 1.0)
    assert hex_to_rgba("nope") is None
