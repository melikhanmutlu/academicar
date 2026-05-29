"""Local development server launcher.

This keeps the standard library ahead of any fallback site-packages path while
allowing the app to reuse dependencies from the nearby development venv.
"""
from __future__ import annotations

import runpy
import sys

FALLBACK_SITE_PACKAGES = (
    r"C:\Users\syste\Desktop\Web & Dev\Projeler\web_ar Projesi\web_ar\.venv\Lib\site-packages"
)

if FALLBACK_SITE_PACKAGES not in sys.path:
    sys.path.append(FALLBACK_SITE_PACKAGES)

runpy.run_path("app.py", run_name="__main__")
