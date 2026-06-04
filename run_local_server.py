"""Local development server launcher.

Runs the Flask app exactly as ``python app.py`` would. An optional
``FALLBACK_SITE_PACKAGES`` environment variable can point at an extra
site-packages directory to append to ``sys.path`` (handy when reusing a
nearby virtualenv's dependencies); it is ignored when unset.
"""
from __future__ import annotations

import os
import runpy
import sys

fallback_site_packages = os.environ.get("FALLBACK_SITE_PACKAGES", "").strip()
if fallback_site_packages and fallback_site_packages not in sys.path:
    sys.path.append(fallback_site_packages)

runpy.run_path("app.py", run_name="__main__")
