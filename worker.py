"""AcademicAR conversion worker entry point.

Production web services only enqueue ConversionJob rows. This process polls
the database and performs CPU/RAM-heavy 3D conversion work in an isolated
Railway/VPS service.
"""

from __future__ import annotations

import os
import time

from app import create_app, run_next_conversion_job


def main() -> None:
    app = create_app()
    interval = float(os.environ.get("WORKER_POLL_INTERVAL", "2"))
    app.logger.info("AcademicAR worker booted. Polling conversion_jobs every %.1fs.", interval)
    while True:
        processed = run_next_conversion_job(app)
        if processed:
            continue
        time.sleep(interval)


if __name__ == "__main__":
    main()
