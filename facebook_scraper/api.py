"""Facebook internal GraphQL calls executed inside the browser context.

Comments live behind ``/api/graphql/``. Requests are form-encoded and must
carry ``fb_dtsg`` / ``lsd`` / ``jazoest`` plus a ``doc_id`` identifying the
persisted query. ``doc_id`` values ROTATE server-side; we centralise them here
and, because the response shape also drifts, parse results by walking the whole
payload (see ``interceptor_config``) rather than a fixed path — so a stale
doc_id degrades to the DOM path instead of silently breaking.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlencode

from core.browser_fetch import browser_fetch
from core.human import human_delay

from .interceptor_config import extract_comments_from_payload, find_page_info

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://www.facebook.com/api/graphql/"

# Persisted-query identifiers for the comment list/pagination. These rotate;
# keep the friendly name + a small fallback list and prefer whatever the live
# page actually used (harvested from the interceptor) when available.
COMMENTS_FRIENDLY_NAME = "CommentsListComponentsPaginationQuery"
COMMENTS_DOC_IDS = (
    "9738666672837349",
    "7060883957335635",
)

# Ordering intent tokens Facebook accepts for the comments connection.
COMMENTS_INTENT_TOKEN = "RANKED_UNFILTERED_CHRONOLOGICAL_REPLIES_INTENT_V1"


def _graphql_headers(tokens: dict[str, str], friendly_name: str) -> dict[str, str]:
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "X-FB-Friendly-Name": friendly_name,
        "X-ASBD-ID": "359341",
        "Accept": "*/*",
    }
    if tokens.get("lsd"):
        headers["X-FB-LSD"] = tokens["lsd"]
    return headers


def _graphql_body(tokens: dict[str, str], friendly_name: str, doc_id: str, variables: dict) -> str:
    fields = {
        "av": tokens.get("av", "0"),
        "__a": "1",
        "__comet_req": "15",
        "fb_dtsg": tokens.get("fb_dtsg", ""),
        "jazoest": tokens.get("jazoest", ""),
        "lsd": tokens.get("lsd", ""),
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": friendly_name,
        "server_timestamps": "true",
        "doc_id": doc_id,
        "variables": json.dumps(variables, separators=(",", ":")),
    }
    if tokens.get("__spin_r"):
        fields["__spin_r"] = tokens["__spin_r"]
    return urlencode(fields)


def _comment_variables(feedback_id: str, cursor: str | None, count: int = 50) -> dict:
    return {
        "commentsIntentToken": COMMENTS_INTENT_TOKEN,
        "feedLocation": "PERMALINK",
        "feedbackSource": 110,
        "focusCommentID": None,
        "scale": 1,
        "useDefaultActor": False,
        "id": feedback_id,
        "first": count,
        "after": cursor,
    }


def harvest_comment_query(captures: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Recover the live comments GraphQL query (doc_id + friendly name +
    variables) from Facebook's OWN captured requests, so we never rely on a
    hardcoded doc_id. Prefers a pagination query (one that carries a cursor)."""
    best: dict[str, Any] | None = None
    for cap in captures:
        rb = cap.get("reqBody") or ""
        if "fb_api_req_friendly_name" not in rb or "doc_id" not in rb:
            continue
        fields = parse_qs(rb)
        fn = (fields.get("fb_api_req_friendly_name") or [""])[0]
        doc_id = (fields.get("doc_id") or [""])[0]
        variables_raw = (fields.get("variables") or [""])[0]
        if not doc_id or not variables_raw or "comment" not in fn.lower():
            continue
        try:
            variables = json.loads(variables_raw)
        except (json.JSONDecodeError, ValueError):
            continue
        has_cursor = isinstance(variables, dict) and any(
            variables.get(k) not in (None, "") for k in ("after", "cursor")
        ) or (isinstance(variables, dict) and ("after" in variables or "cursor" in variables))
        cand = {"friendly_name": fn, "doc_id": doc_id, "variables": variables, "has_cursor": has_cursor}
        if best is None or (has_cursor and not best["has_cursor"]):
            best = cand
    if best:
        logger.info("Harvested live comment query: %s (doc_id=%s)", best["friendly_name"], best["doc_id"])
    return best


def harvest_all_comment_queries(captures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recover every distinct comment/reply GraphQL query FB fired (top-level
    pagination + each reply thread), deduped by doc_id + target id."""
    out: dict[str, dict[str, Any]] = {}
    for cap in captures:
        rb = cap.get("reqBody") or ""
        if "fb_api_req_friendly_name" not in rb or "doc_id" not in rb:
            continue
        fields = parse_qs(rb)
        fn = (fields.get("fb_api_req_friendly_name") or [""])[0]
        doc_id = (fields.get("doc_id") or [""])[0]
        variables_raw = (fields.get("variables") or [""])[0]
        if not doc_id or not variables_raw or "comment" not in fn.lower():
            continue
        try:
            variables = json.loads(variables_raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(variables, dict):
            continue
        target = str(variables.get("id") or variables.get("feedbackID") or variables.get("commentID") or "")
        sig = f"{doc_id}:{target}"
        out[sig] = {"friendly_name": fn, "doc_id": doc_id, "variables": variables}
    if out:
        logger.info("Harvested %d distinct comment quer(y/ies) from captures", len(out))
    return list(out.values())


def paginate_with_query(
    driver,
    tokens: dict[str, str],
    query: dict[str, Any],
    max_pages: int = 60,
    delay_range: tuple[float, float] = (0.8, 1.8),
) -> list[dict[str, Any]]:
    """Paginate using a harvested query template, advancing its cursor."""
    friendly = query["friendly_name"]
    doc_id = query["doc_id"]
    variables = dict(query["variables"])
    cursor_key = "after" if "after" in variables or "cursor" not in variables else "cursor"

    all_comments: dict[str, dict[str, Any]] = {}
    cursor = variables.get(cursor_key)
    for page in range(max_pages):
        human_delay(*delay_range)
        variables[cursor_key] = cursor
        body = _graphql_body(tokens, friendly, doc_id, variables)
        resp = browser_fetch(driver, GRAPHQL_URL, method="POST", body=body, headers=_graphql_headers(tokens, friendly))
        payload = resp.get("json")
        if not payload:
            logger.warning("Harvested query returned no JSON (page %d); stopping", page)
            break
        for c in extract_comments_from_payload(payload):
            key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
            all_comments[key] = c
        page_info = find_page_info(payload)
        if not page_info or not page_info.get("has_next_page"):
            break
        cursor = page_info.get("end_cursor") or page_info.get("cursor")
        if not cursor:
            break
    logger.info("Harvested-query pagination collected %d comments", len(all_comments))
    return list(all_comments.values())


def get_feedback_comments(
    driver,
    feedback_id: str,
    tokens: dict[str, str],
    max_pages: int = 50,
    delay_range: tuple[float, float] = (0.8, 1.8),
    doc_ids: tuple[str, ...] = COMMENTS_DOC_IDS,
) -> list[dict[str, Any]]:
    """Paginate the comments connection for a feedback target."""
    all_comments: dict[str, dict[str, Any]] = {}
    cursor: str | None = None
    doc_id = doc_ids[0]

    for page in range(max_pages):
        human_delay(*delay_range)
        variables = _comment_variables(feedback_id, cursor)
        body = _graphql_body(tokens, COMMENTS_FRIENDLY_NAME, doc_id, variables)
        resp = browser_fetch(
            driver,
            GRAPHQL_URL,
            method="POST",
            body=body,
            headers=_graphql_headers(tokens, COMMENTS_FRIENDLY_NAME),
        )
        payload = resp.get("json")

        # First page with a dead doc_id: try the fallbacks before giving up.
        if not payload and page == 0:
            for alt in doc_ids[1:]:
                body = _graphql_body(tokens, COMMENTS_FRIENDLY_NAME, alt, variables)
                resp = browser_fetch(
                    driver,
                    GRAPHQL_URL,
                    method="POST",
                    body=body,
                    headers=_graphql_headers(tokens, COMMENTS_FRIENDLY_NAME),
                )
                payload = resp.get("json")
                if payload:
                    doc_id = alt
                    break

        if not payload:
            logger.warning("GraphQL comments returned no JSON (page %d); stopping API path", page)
            break

        new = extract_comments_from_payload(payload)
        for c in new:
            key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
            all_comments[key] = c

        page_info = find_page_info(payload)
        if not page_info or not page_info.get("has_next_page"):
            break
        cursor = page_info.get("end_cursor") or page_info.get("cursor")
        if not cursor:
            break

        if page and page % 5 == 0:
            logger.info("Comment pagination page %d: %d total", page, len(all_comments))

    logger.info("GraphQL extracted %d comments for feedback %s", len(all_comments), feedback_id)
    return list(all_comments.values())
