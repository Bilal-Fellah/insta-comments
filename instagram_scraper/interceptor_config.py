"""Instagram-specific network capture config + payload comment extraction."""

from __future__ import annotations

from typing import Any

from core.capture import build_interceptor_js, parse_json_body, walk_collect
from core.capture import drain_captured_responses as _drain

__all__ = [
    "INTERCEPTOR_JS",
    "IG_APP_ID",
    "CAPTURE_GLOBAL",
    "drain_captured_responses",
    "parse_json_body",
    "extract_comments_from_payload",
]

IG_APP_ID = "936619743392459"

# Namespaces window globals as window.__igInterceptor / window.__igCaptured.
CAPTURE_GLOBAL = "ig"
CAPTURE_MATCHERS = ["/graphql", "/api/v1/", "comments", "comment"]

INTERCEPTOR_JS = build_interceptor_js(CAPTURE_MATCHERS, CAPTURE_GLOBAL)


def drain_captured_responses(driver) -> list[dict[str, Any]]:
    return _drain(driver, CAPTURE_GLOBAL)


def _extract_comment(node: dict) -> dict[str, Any] | None:
    user = node.get("user") or node.get("owner") or {}
    username = None
    if isinstance(user, dict):
        username = user.get("username") or user.get("full_name")
    if "text" not in node or not username:
        return None
    pk = node.get("pk") or node.get("id")
    return {
        "id": str(pk) if pk else None,
        "username": username,
        "text": node.get("text"),
        "timestamp": node.get("created_at") or node.get("created_at_utc"),
        "likes": node.get("comment_like_count") or node.get("like_count") or 0,
        "is_reply": bool(node.get("parent_comment_id") or node.get("replied_to_comment_id")),
        "parent_id": node.get("parent_comment_id") or node.get("replied_to_comment_id"),
    }


def extract_comments_from_payload(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def visit(node: dict) -> None:
        c = _extract_comment(node)
        if c is not None:
            found.append(c)

    walk_collect(payload, visit)

    dedup: dict[str, dict[str, Any]] = {}
    for c in found:
        key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
        dedup[key] = c
    return list(dedup.values())
