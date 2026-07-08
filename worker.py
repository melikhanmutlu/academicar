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
    # Monthly institution usage reports piggyback on the worker (email +
    # aggregate queries stay out of web requests). The check itself is cheap
    # and internally once-per-calendar-month, so a coarse timer suffices.
    report_check_interval = float(os.environ.get("INSTITUTION_REPORT_CHECK_INTERVAL", "900"))
    last_report_check = 0.0
    app.logger.info("AcademicAR worker booted. Polling conversion_jobs every %.1fs.", interval)
    while True:
        try:
            processed = run_next_conversion_job(app)
        except Exception:
            app.logger.exception("Unexpected error in conversion job processing")
            processed = False
        if time.monotonic() - last_report_check >= report_check_interval:
            last_report_check = time.monotonic()
            try:
                with app.app_context():
                    from institutions import send_monthly_institution_reports

                    count = send_monthly_institution_reports()
                    if count:
                        app.logger.info("Sent monthly usage reports for %d institution(s).", count)
            except Exception:
                app.logger.exception("Unexpected error in institution usage reports")
        if processed:
            continue
        time.sleep(interval)


if __name__ == "__main__":
    main()
