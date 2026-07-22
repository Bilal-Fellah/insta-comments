"""Facebook-specific network capture config + payload comment extraction.

Facebook's GraphQL/Comet responses nest comment nodes deeply and the exact
shape rotates. Rather than target a fixed path, we walk the whole payload and
recognise any node that looks like a comment (has an ``author`` name and a body
text), mirroring the robust approach used for Instagram.
"""

from __future__ import annotations

from typing import Any

from core.capture import build_interceptor_js, parse_json_body, walk_collect
from core.capture import drain_captured_responses as _drain

__all__ = [
    "INTERCEPTOR_JS",
    "CAPTURE_GLOBAL",
    "drain_captured_responses",
    "parse_json_body",
    "extract_comments_from_payload",
    "find_page_info",
    "find_feedback_id",
]

CAPTURE_GLOBAL = "fb"
CAPTURE_MATCHERS = ["/api/graphql/", "/ajax/", "comet", "Comment", "feedback"]

INTERCEPTOR_JS = build_interceptor_js(CAPTURE_MATCHERS, CAPTURE_GLOBAL)


def drain_captured_responses(driver) -> list[dict[str, Any]]:
    return _drain(driver, CAPTURE_GLOBAL)


def _node_text(node: dict) -> str | None:
    body = node.get("body")
    if isinstance(body, dict) and isinstance(body.get("text"), str):
        return body["text"]
    if isinstance(node.get("text"), str) and "author" in node:
        return node["text"]
    return None


def _node_reactions(node: dict) -> int:
    for key in ("feedback", "reaction_count", "comment_reactions"):
        obj = node.get(key)
        if isinstance(obj, dict):
            reactors = obj.get("reactors") or obj.get("reaction_count") or obj
            if isinstance(reactors, dict) and isinstance(reactors.get("count"), int):
                return reactors["count"]
            if isinstance(obj.get("count"), int):
                return obj["count"]
    return 0


def _extract_comment(node: dict) -> dict[str, Any] | None:
    author = node.get("author")
    if not isinstance(author, dict):
        return None
    name = author.get("name") or author.get("short_name")
    text = _node_text(node)
    if not name or text is None:
        return None

    cid = node.get("legacy_fbid") or node.get("id")
    created = node.get("created_time") or node.get("created_at") or node.get("timestamp")

    parent = node.get("comment_parent") or node.get("parent_comment")
    parent_id = None
    if isinstance(parent, dict):
        parent_id = parent.get("legacy_fbid") or parent.get("id")
    depth = node.get("depth")

    return {
        "id": str(cid) if cid else None,
        "username": name,
        "text": text,
        "timestamp": created,
        "likes": _node_reactions(node),
        "is_reply": bool(parent_id) or bool(depth),
        "parent_id": str(parent_id) if parent_id else None,
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


def find_page_info(payload: Any) -> dict[str, Any] | None:
    """Return a ``page_info`` dict with a usable cursor, if the payload has one."""
    candidates: list[dict[str, Any]] = []

    def visit(node: dict) -> None:
        if "has_next_page" in node and ("end_cursor" in node or "cursor" in node):
            candidates.append(node)

    walk_collect(payload, visit)
    # Prefer one that says there IS a next page and carries a cursor.
    for node in candidates:
        if node.get("has_next_page") and (node.get("end_cursor") or node.get("cursor")):
            return node
    return candidates[0] if candidates else None


def find_feedback_id(payload: Any) -> str | None:
    """Locate a feedback id (the comment-thread target) anywhere in a payload."""
    result: list[str] = []

    def visit(node: dict) -> None:
        if result:
            return
        fb = node.get("feedback")
        if isinstance(fb, dict) and isinstance(fb.get("id"), str):
            result.append(fb["id"])
        elif isinstance(node.get("feedback_target_id"), str):
            result.append(node["feedback_target_id"])

    walk_collect(payload, visit)
    return result[0] if result else None
