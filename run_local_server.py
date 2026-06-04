"""Local development server launcher."""
from __future__ import annotations

import runpy

runpy.run_path("app.py", run_name="__main__")
