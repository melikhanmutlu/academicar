"""Send one test email through the real send_email() path.

Every lifecycle/worker email (welcome, license-renewal reminders, R2 mirror
alerts, institution reports/invites) goes through ``utils.email.send_email``.
This script exercises that exact path once so you can confirm delivery works
end to end — which backend is active, which From address is used, and whether
the provider accepted the message — without waiting for a real trigger.

It prints the resolved sender and backend BEFORE sending, so even a failure
tells you what was attempted.

Usage:
    python scripts/send_test_email.py you@example.com

On Railway (so it reads the service's env vars):
    railway run python scripts/send_test_email.py you@example.com
"""

from __future__ import annotations

import os
import sys

# Allow running as a plain script: make the repo root importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        print("Usage: python scripts/send_test_email.py <recipient@example.com>")
        return 2
    recipient = sys.argv[1].strip()

    app = create_app()
    with app.app_context():
        from utils.email import _resolve_sender, send_email

        api_key = app.config.get("MAIL_API_KEY")
        server = app.config.get("MAIL_SERVER")
        if api_key:
            backend = f"HTTP API ({app.config.get('MAIL_API_URL')})"
        elif server:
            backend = f"SMTP ({server}:{app.config.get('MAIL_PORT')})"
        else:
            backend = "NONE — email is only logged, nothing is delivered"

        sender = _resolve_sender()
        print("── AcademicAR email test ─────────────────────────────")
        print(f"Backend : {backend}")
        print(f"From    : {sender}")
        print(f"To      : {recipient}")
        print("──────────────────────────────────────────────────────")

        if not (api_key or server):
            print(
                "No mail backend configured. Set MAIL_API_KEY (Brevo) — or "
                "MAIL_SERVER for SMTP — and re-run. Nothing was sent."
            )
            return 1

        ok = send_email(
            recipient,
            "AcademicAR test email",
            "This is a test email from AcademicAR.\n\n"
            "If you received it, transactional email is configured correctly "
            "and lifecycle/worker emails will be delivered.",
        )
        if ok:
            print("RESULT  : accepted by the backend ✅  (check the inbox, and spam)")
            return 0
        print(
            "RESULT  : the backend REJECTED the message ❌\n"
            "Most common cause with Brevo: the From address is not a verified "
            "sender/domain. Verify the sender in the Brevo dashboard, or point "
            "MAIL_DEFAULT_SENDER at an address you have verified. Check the app "
            "logs for the exact provider error."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
