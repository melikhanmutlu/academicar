"""AcademicAR conversion worker entry point.

Production web services only enqueue ConversionJob rows. This process polls
the database and performs CPU/RAM-heavy 3D conversion work in an isolated
Railway/VPS service. It also runs periodic maintenance (purging soft-deleted
publications past their grace period) so the web process stays request-only.
"""

from __future__ import annotations

import os
import time

from app import create_app, purge_soft_deleted_papers, run_next_conversion_job


def main() -> None:
    app = create_app()
    interval = float(os.environ.get("WORKER_POLL_INTERVAL", "2"))
    maintenance_interval = float(os.environ.get("WORKER_MAINTENANCE_INTERVAL", "3600"))
    app.logger.info(
        "AcademicAR worker booted. Polling conversion_jobs every %.1fs; "
        "maintenance every %.0fs.",
        interval,
        maintenance_interval,
    )
    last_maintenance = 0.0
    while True:
        now = time.monotonic()
        if now - last_maintenance >= maintenance_interval:
            try:
                purge_soft_deleted_papers(app)
            except Exception:  # pragma: no cover - defensive, never kill the loop
                app.logger.exception("Periodic maintenance failed")
            last_maintenance = now

        processed = run_next_conversion_job(app)
        if processed:
            continue
        time.sleep(interval)


if __name__ == "__main__":
    main()
