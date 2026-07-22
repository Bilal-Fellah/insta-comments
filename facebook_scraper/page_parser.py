"""Extract comments/feedback id embedded in Facebook page HTML.

Modern Facebook ships its initial data inside ``<script type="application/json"
data-sjs>`` RelayPrefetchedStreamCache blocks. We parse those JSON blobs and
reuse the payload walker to pull out comments and the feedback id.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .interceptor_config import extract_comments_from_payload, find_feedback_id

logger = logging.getLogger(__name__)

_SCRIPT_JSON_RE = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_FEEDBACK_ID_RE = re.compile(r'"feedback"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"')
_FEEDBACK_TARGET_RE = re.compile(r'"feedback_target_id"\s*:\s*"([^"]+)"')


def _iter_script_json(source: str):
    for match in _SCRIPT_JSON_RE.findall(source):
        blob = match.strip()
        if not blob or '"' not in blob:
            continue
        try:
            yield json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            continue


def extract_feedback_id_from_source(source: str) -> str | None:
    for payload in _iter_script_json(source):
        fid = find_feedback_id(payload)
        if fid:
            return fid
    m = _FEEDBACK_ID_RE.search(source) or _FEEDBACK_TARGET_RE.search(source)
    return m.group(1) if m else None


def extract_comments_from_page_source(source: str) -> list[dict[str, Any]]:
    comments: dict[str, dict[str, Any]] = {}
    for payload in _iter_script_json(source):
        for c in extract_comments_from_payload(payload):
            key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
            comments[key] = c
    found = list(comments.values())
    if found:
        logger.info("Extracted %d comments from embedded page JSON", len(found))
    return found
