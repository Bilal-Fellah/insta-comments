#!/usr/bin/env bash
set -euo pipefail

# Start Xvfb manually — xvfb-run's SIGUSR1/wait mechanism is unreliable in Docker.
# We start Xvfb in the background, poll its lock file until ready, then exec python.
DISPLAY_NUM=99
export DISPLAY=":${DISPLAY_NUM}"

Xvfb ":${DISPLAY_NUM}" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait up to 10 seconds for Xvfb to create its lock file (signals it's ready)
for i in $(seq 1 20); do
    if [ -f "/tmp/.X${DISPLAY_NUM}-lock" ]; then
        echo "[entrypoint] Xvfb :${DISPLAY_NUM} ready (attempt ${i})"
        break
    fi
    sleep 0.5
done

if [ ! -f "/tmp/.X${DISPLAY_NUM}-lock" ]; then
    echo "[entrypoint] ERROR: Xvfb failed to start (no lock file after 10s)" >&2
    exit 1
fi

# Run the scraper; Xvfb is cleaned up when container exits
exec python scrape_instagram.py "$@"
