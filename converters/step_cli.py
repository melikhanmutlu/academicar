"""Run cascadio (OpenCascade) STEP -> GLB in its own process.

OpenCascade can segfault or exhaust memory on hostile/huge STEP files, so
STEPConverter shells out to this module instead of importing cascadio in the
worker. Exit code: 0 ok, 2 conversion failed.
"""

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--tol-linear", type=float, default=0.05)
    parser.add_argument("--tol-angular", type=float, default=0.2)
    args = parser.parse_args(argv)

    import cascadio

    try:
        result = cascadio.step_to_glb(
            args.input,
            args.output,
            tol_linear=args.tol_linear,
            tol_angular=args.tol_angular,
            merge_primitives=True,
            use_parallel=True,
        )
    except Exception as exc:  # cascadio raises a plain RuntimeError on bad input
        print(f"cascadio failed: {exc}", file=sys.stderr)
        return 2
    if result != 0:
        print(f"cascadio returned {result}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
