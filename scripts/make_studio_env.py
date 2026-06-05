#!/usr/bin/env python3
"""Generate a soft studio equirectangular environment image for model-viewer IBL.

Produces a 1024x512 LDR image with:
- Warm-white top light (simulates overhead softbox)
- Neutral mid-gray horizon (no color cast)
- Subtle floor bounce (slightly warm)

Output: static/environments/studio.webp
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image

WIDTH = 1024
HEIGHT = 512
OUT = Path(__file__).resolve().parent.parent / "static" / "environments" / "studio.webp"


def make_studio_env() -> Path:
    img = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)

    for y in range(HEIGHT):
        # v: 0 at top (zenith), 1 at bottom (nadir)
        v = y / (HEIGHT - 1)
        # theta: 0 at zenith, pi at nadir
        theta = v * np.pi

        # --- Uniform ambient floor: lifts every direction so no side of the
        # model goes dark (the previous studio map left the sides ~0.4 and
        # looked too shadowy). ---
        ambient = 0.47

        # --- Sky dome: soft warm-white overhead light ---
        # A more concentrated, brighter key light (narrower Gaussian) gives a
        # crisper specular streak so surface curvature reads better and models
        # look less flat/"pastel" — without raising the ambient fill that would
        # wash detail back out.
        overhead = 0.60 * np.exp(-0.5 * (theta / 0.42) ** 2)

        # --- Horizon band: gentle brightening around eye level ---
        horizon = 0.20 * np.exp(-0.5 * ((theta - np.pi / 2) / 0.5) ** 2)

        # --- Floor bounce: soft fill from below ---
        floor_angle = np.pi - theta
        bounce = 0.18 * np.exp(-0.5 * (floor_angle / 0.7) ** 2)

        # Combine
        base = ambient + overhead + horizon + bounce
        r = base + overhead * 0.03 + bounce * 0.02
        g = base + overhead * 0.015
        b = base - overhead * 0.01

        img[y, :, 0] = np.clip(r, 0, 1)
        img[y, :, 1] = np.clip(g, 0, 1)
        img[y, :, 2] = np.clip(b, 0, 1)

    img_uint8 = (img * 255).astype(np.uint8)
    pil_img = Image.fromarray(img_uint8, "RGB")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pil_img.save(str(OUT), "WEBP", quality=90)
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
    return OUT


if __name__ == "__main__":
    make_studio_env()
