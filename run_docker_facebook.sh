#!/usr/bin/env bash
# Run the Facebook comment scraper in Docker with cookies for an authenticated session.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-instagram_comment-scraper}"
POST_URL="${1:-}"

if [[ -z "$POST_URL" ]]; then
    echo "Usage: $0 <facebook-post-url>"
    echo "Example: $0 'https://www.facebook.com/NASA/posts/pfbid0XXXX/'"
    exit 1
fi

mkdir -p "$ROOT/output" "$ROOT/cookies"

# Prefer a mounted local config with your credentials; fall back to docker default.
if [[ -f "$ROOT/config.facebook.local.yaml" ]]; then
    CONFIG_HOST="$ROOT/config.facebook.local.yaml"
    CONFIG_CONT="/app/config.facebook.local.yaml"
else
    CONFIG_HOST="$ROOT/config.facebook.docker.yaml"
    CONFIG_CONT="/app/config.facebook.docker.yaml"
fi

# Cookies dir is mounted READ-WRITE so a first successful login persists the
# session to the host — subsequent runs reuse it and skip logging in.
DOCKER_FLAGS=(
    --rm
    --shm-size=2g
    -v "$ROOT/output:/app/output"
    -v "$ROOT/cookies:/app/cookies"
    -v "$CONFIG_HOST:$CONFIG_CONT:ro"
    -e IN_DOCKER=1
    -e PLATFORM=facebook
    -e FB_HEADLESS=1
    -e FB_USER_DATA_DIR=none
)

SCRAPER_ARGS=(
    --config "$CONFIG_CONT"
    --post-url "$POST_URL"
    --cookies-file /app/cookies/facebook_cookies.json
    -v
)

if [[ -f "$ROOT/cookies/facebook_cookies.json" ]]; then
    echo "[run] Reusing saved session: $ROOT/cookies/facebook_cookies.json"
else
    echo "[run] No cookies yet — will log in once and SAVE cookies for reuse."
fi

exec docker run "${DOCKER_FLAGS[@]}" "$IMAGE" "${SCRAPER_ARGS[@]}"
