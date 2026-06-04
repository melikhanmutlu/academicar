#!/usr/bin/env sh
# Production launcher for a single Railway service running both the web server
# and the conversion worker. Prefer two separate Railway services using the
# Procfile's `web` and `worker` entries when you can; this script is the
# robust single-container fallback.
#
# Improvements over the previous inline `... & gunicorn ...` command:
#   * migrations run synchronously first, so the web never serves against a
#     half-migrated schema (and a bad migration fails the deploy loudly);
#   * the worker runs under an auto-restart loop, so a crash doesn't silently
#     stop all conversions for the life of the container;
#   * SIGTERM/SIGINT are forwarded to both children for a clean shutdown.
set -e

flask db upgrade

WORKER_PID=""
WEB_PID=""

terminate() {
    [ -n "$WORKER_PID" ] && kill "$WORKER_PID" 2>/dev/null || true
    [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null || true
}
trap terminate TERM INT

# Conversion worker with auto-restart.
(
    while true; do
        python worker.py || true
        echo "[start.sh] worker exited; restarting in 3s" >&2
        sleep 3
    done
) &
WORKER_PID=$!

# Web server in the foreground; if it dies the container exits and Railway
# restarts it (per restartPolicy).
gunicorn app:app --bind 0.0.0.0:"${PORT:-8000}" \
    --workers 1 --worker-class gthread --threads 8 --timeout 180 &
WEB_PID=$!

wait "$WEB_PID"
terminate
