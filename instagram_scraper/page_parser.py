"""Extract comments embedded in Instagram post page HTML."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

COMMENT_OBJECT_RE = re.compile(
    r'\{"pk":"(\d+)"[^\}]*?"user":\{[^\}]*?"username":"([^"]+)"[^\}]*?\}[^\}]*?"text":"((?:\\.|[^"\\])*)"',
    re.DOTALL,
)


def _decode_json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.encode("utf-8").decode("unicode_escape")


def extract_comments_from_page_source(source: str) -> list[dict[str, Any]]:
    comments: dict[str, dict[str, Any]] = {}

    for pk, username, text in COMMENT_OBJECT_RE.findall(source):
        comments[pk] = {
            "id": pk,
            "username": username,
            "text": _decode_json_string(text),
            "timestamp": None,
            "likes": 0,
            "is_reply": False,
            "parent_id": None,
        }

    # Timestamps / likes near pk blocks
    for pk in list(comments.keys()):
        chunk_match = re.search(rf'"pk":"{pk}"[\s\S]{{0,1200}}', source)
        if not chunk_match:
            continue
        chunk = chunk_match.group(0)
        ts = re.search(r'"created_at(?:_utc)?":(\d+)', chunk)
        likes = re.search(r'"comment_like_count":(\d+)', chunk)
        if ts:
            comments[pk]["timestamp"] = int(ts.group(1))
        if likes:
            comments[pk]["likes"] = int(likes.group(1))

    found = list(comments.values())
    if found:
        logger.info("Extracted %d comments from embedded page JSON", len(found))
    return found
