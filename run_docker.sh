#!/usr/bin/env bash
# Run the Instagram comment scraper in Docker with cookies for authenticated session.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-instagram_comment-scraper}"
POST_URL="${1:-}"

if [[ -z "$POST_URL" ]]; then
    echo "Usage: $0 <instagram-post-url>"
    echo "Example: $0 'https://www.instagram.com/fcbarcelonab/p/DaTFb1NtGWP/'"
    exit 1
fi

mkdir -p "$ROOT/output"

# Build docker flags and scraper args separately
DOCKER_FLAGS=(
    --rm
    --shm-size=2g
    -v "$ROOT/output:/app/output"
    -v "$ROOT/config.local.yaml:/app/config.local.yaml:ro"
    -e IN_DOCKER=1
    -e IG_HEADLESS=1
    -e IG_USER_DATA_DIR=none
)

SCRAPER_ARGS=(
    --config config.local.yaml
    --post-url "$POST_URL"
    -v
)

if [[ -f "$ROOT/cookies/instagram_cookies.json" ]]; then
    echo "[run] Authenticated mode: using $ROOT/cookies/instagram_cookies.json"
    DOCKER_FLAGS+=(-v "$ROOT/cookies:/app/cookies:ro")
    SCRAPER_ARGS+=(--cookies-file /app/cookies/instagram_cookies.json)
else
    echo "[run] No cookies found — will attempt credential login (may hit CAPTCHA in Docker)"
fi

exec docker run "${DOCKER_FLAGS[@]}" "$IMAGE" "${SCRAPER_ARGS[@]}"
