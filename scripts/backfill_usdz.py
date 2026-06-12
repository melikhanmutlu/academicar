"""Backfill iOS USDZ companions for existing models.

Models uploaded before USDZ generation was enabled have a ``model.glb`` but no
``model.usdz``, so iOS AR (especially Chrome on iOS) can't open them. This
script walks the Model3D rows, pulls each ``model.glb`` local (from the R2
mirror if needed), generates ``model.usdz`` with headless Blender, and mirrors
the result back so the public viewer serves it as ``ios-src``.

Requires the ``blender`` binary on PATH — run it in the production/worker
container (Railway), not local dev unless Blender is installed.

Usage:
    python scripts/backfill_usdz.py              # ready models missing USDZ
    python scripts/backfill_usdz.py --force      # regenerate even if present
    python scripts/backfill_usdz.py --model <id> # a single model
    python scripts/backfill_usdz.py --limit 50   # cap how many are processed
    python scripts/backfill_usdz.py --dry-run    # report only, generate nothing
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

# Allow running as a plain script: make the repo root importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402
from converters.stl_converter import convert_glb_to_usdz  # noqa: E402
from models import Model3D  # noqa: E402
from services.r2_mirror import ensure_local, mirror_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill iOS USDZ companions.")
    parser.add_argument("--force", action="store_true", help="Regenerate even if a USDZ already exists.")
    parser.add_argument("--model", help="Only process this single model id.")
    parser.add_argument("--limit", type=int, default=0, help="Process at most this many models (0 = all).")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen; generate nothing.")
    args = parser.parse_args()

    if shutil.which("blender") is None and not args.dry_run:
        print(
            "ERROR: 'blender' is not on PATH. Run this in the production/worker "
            "container where Blender is installed (nixpacks), or use --dry-run.",
            file=sys.stderr,
        )
        return 2

    app = create_app()
    with app.app_context():
        converted = app.config["CONVERTED_FOLDER"]

        query = Model3D.query
        if args.model:
            query = query.filter(Model3D.id == args.model)
        else:
            query = query.filter(Model3D.processing_status == "ready")
        query = query.order_by(Model3D.created_at.asc())
        if args.limit:
            query = query.limit(args.limit)
        models = query.all()

        print(f"Inspecting {len(models)} model(s)...")
        generated = skipped = missing_glb = failed = 0

        for model in models:
            model_dir = os.path.join(converted, model.id)
            glb_path = os.path.join(model_dir, "model.glb")
            usdz_path = os.path.join(model_dir, "model.usdz")
            r2_glb = f"converted/{model.id}/model.glb"
            r2_usdz = f"converted/{model.id}/model.usdz"

            # Already has a USDZ (local or mirrored)? Skip unless forced.
            if not args.force and (
                os.path.exists(usdz_path) or ensure_local(usdz_path, r2_usdz)
            ):
                skipped += 1
                continue

            # Make sure the source GLB is on the local volume.
            if not os.path.exists(glb_path):
                ensure_local(glb_path, r2_glb)
            if not os.path.exists(glb_path):
                print(f"  [missing GLB] {model.id} — no model.glb locally or in mirror")
                missing_glb += 1
                continue

            if args.dry_run:
                print(f"  [would generate] {model.id}")
                generated += 1
                continue

            os.makedirs(model_dir, exist_ok=True)
            ok = False
            try:
                ok = convert_glb_to_usdz(glb_path, usdz_path)
            except Exception as exc:  # noqa: BLE001 - keep going across the batch
                print(f"  [error] {model.id} — {exc}")

            if ok:
                mirror_file(usdz_path, r2_usdz)
                print(f"  [generated] {model.id}")
                generated += 1
            else:
                print(f"  [failed] {model.id} — Blender produced no USDZ")
                failed += 1

        print(
            "\nDone. "
            f"generated={generated} skipped={skipped} "
            f"missing_glb={missing_glb} failed={failed}"
        )
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
