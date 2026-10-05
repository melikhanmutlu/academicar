"""Progress from the conversion child process to the worker.

The child writes ``{"percent": 0-100, "stage": "..."}`` to a small file the
parent gave it (``--progress``); the parent polls it while the child runs. A
file keeps the child's stdout/stderr (kept free of anything but errors) and its
result JSON untouched, and a killed child simply leaves the last value behind.

Stage labels are fixed phrases plus counts, and the preset's own label (Bone,
Skin, ...). They never contain file names, DICOM header values or anything
else that could identify a patient.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

_WRITE_INTERVAL = 0.2
_state = {"path": None, "key": None, "at": 0.0, "peak": 0.0}


def set_sink(path: str | None) -> None:
    """Child side: where ``report`` writes. ``None`` turns reporting off."""
    _state.update(path=path or None, key=None, at=0.0, peak=0.0)


def report(percent: float, stage: str, *, force: bool = False) -> None:
    """Child side: publish progress. Throttled except when the stage wording changes (digits ignored)."""
    path = _state["path"]
    if not path:
        return
    # Never go backwards (e.g. when meshing is retried at a coarser resolution).
    percent = _state["peak"] = max(_state["peak"], float(percent))
    key = re.sub(r"\d+", "#", stage)
    now = time.monotonic()
    if not force and key == _state["key"] and now - _state["at"] < _WRITE_INTERVAL:
        return
    _state.update(key=key, at=now)
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"percent": max(0.0, min(100.0, float(percent))), "stage": stage}, fh)
        os.replace(tmp, path)  # the parent never reads a half-written file
    except OSError:
        pass  # cosmetic: never fail a conversion over it


def read(path: str) -> tuple[float, str] | None:
    """Parent side: the latest ``(percent, stage)`` or ``None`` when nothing was written yet."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return float(data["percent"]), str(data["stage"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


class Poller:
    """Parent side: calls ``callback(percent, stage)`` when the child's progress file changes."""

    def __init__(self, path: str, callback, interval: float = 0.5):
        self._path = path
        self._callback = callback
        self._interval = interval
        self._stop = threading.Event()
        self._last = None
        self._thread = threading.Thread(target=self._run, name="medical-progress", daemon=True)

    def _poll(self) -> None:
        current = read(self._path)
        if current is not None and current != self._last:
            self._last = current
            try:
                self._callback(*current)
            except Exception:  # progress is cosmetic
                pass

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self._poll()

    def __enter__(self) -> "Poller":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._poll()  # the last value written before the child exited
