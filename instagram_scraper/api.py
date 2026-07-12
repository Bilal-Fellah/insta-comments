"""Instagram internal API calls executed inside the browser context."""

from __future__ import annotations

import json
import logging
from typing import Any

from .human import human_delay

logger = logging.getLogger(__name__)

IG_APP_ID = "936619743392459"

FETCH_JS = """
const [url, method, body] = arguments;
const cb = arguments[arguments.length - 1];
const csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1];
const headers = {
  'X-IG-App-ID': '%s',
  'X-Requested-With': 'XMLHttpRequest',
  'X-ASBD-ID': '359341',
  'Accept': '*/*',
  'Referer': window.location.href,
};
if (csrf) headers['X-CSRFToken'] = csrf;
fetch(url, { method: method || 'GET', headers, credentials: 'include', body: body || undefined })
  .then(async (r) => ({ status: r.status, text: await r.text(), contentType: r.headers.get('content-type') || '' }))
  .then(cb)
  .catch((e) => cb({ status: 0, text: String(e), contentType: '' }));
""" % IG_APP_ID


def browser_fetch(driver, url: str, method: str = "GET", body: str | None = None) -> dict[str, Any]:
    result = driver.execute_async_script(FETCH_JS, url, method, body)
    status = result.get("status", 0)
    text = result.get("text", "")
    content_type = result.get("contentType", "")
    if status >= 400 or (text.startswith("<!DOCTYPE") and "json" not in content_type.lower()):
        logger.warning("Fetch %s returned HTTP %s (non-JSON): %s", url, status, text[:120])
    payload = None
    if text and not text.lstrip().startswith("<!DOCTYPE"):
        try:
            payload = json.loads(text.lstrip("for (;;);"))
        except json.JSONDecodeError:
            payload = None
    return {"status": status, "text": text, "json": payload, "content_type": content_type}


def get_profile_timeline(driver, username: str, delay_range: tuple[float, float] = (1.0, 2.0)) -> list[dict[str, Any]]:
    human_delay(*delay_range)
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    resp = browser_fetch(driver, url)
    data = resp.get("json") or {}
    user = (data.get("data") or {}).get("user") or {}
    edges = ((user.get("edge_owner_to_timeline_media") or {}).get("edges")) or []
    posts: list[dict[str, Any]] = []
    for edge in edges:
        node = edge.get("node") or {}
        shortcode = node.get("shortcode")
        if not shortcode:
            continue
        posts.append(
            {
                "shortcode": shortcode,
                "url": f"https://www.instagram.com/p/{shortcode}/",
                "id": node.get("id"),
                "caption": ((node.get("edge_media_to_caption") or {}).get("edges") or [{}])[0]
                .get("node", {})
                .get("text"),
                "timestamp": node.get("taken_at_timestamp"),
                "like_count": (node.get("edge_liked_by") or node.get("edge_media_preview_like") or {}).get("count"),
                "comment_count": (node.get("edge_media_to_comment") or {}).get("count"),
                "is_video": node.get("is_video"),
            }
        )
    logger.info("API returned %d timeline posts for @%s", len(posts), username)
    return posts


def get_media_comments(
    driver,
    media_id: str,
    shortcode: str,
    max_pages: int = 50,
    delay_range: tuple[float, float] = (0.8, 1.8),
) -> list[dict[str, Any]]:
    """Paginate /api/v1/media/{id}/comments/ endpoint."""
    all_comments: dict[str, dict[str, Any]] = {}
    min_id: str | None = None

    for page in range(max_pages):
        human_delay(*delay_range)
        base = f"https://www.instagram.com/api/v1/media/{media_id}/comments/"
        url = f"{base}?can_support_threading=true&permalink=/p/{shortcode}/"
        if min_id:
            url += f"&min_id={min_id}"

        resp = browser_fetch(driver, url)
        payload = resp.get("json") or {}
        comments = payload.get("comments") or []
        if not comments:
            break

        for c in comments:
            user = c.get("user") or {}
            cid = str(c.get("pk") or c.get("id") or "")
            all_comments[cid or f"{user.get('username')}:{c.get('text')}"] = {
                "id": cid or None,
                "username": user.get("username"),
                "text": c.get("text"),
                "timestamp": c.get("created_at"),
                "likes": c.get("comment_like_count", 0),
                "is_reply": bool(c.get("parent_comment_id")),
                "parent_id": c.get("parent_comment_id"),
            }
            # Child comments / preview replies
            for child in c.get("preview_child_comments") or []:
                cu = child.get("user") or {}
                child_id = str(child.get("pk") or child.get("id") or "")
                all_comments[child_id or f"{cu.get('username')}:{child.get('text')}"] = {
                    "id": child_id or None,
                    "username": cu.get("username"),
                    "text": child.get("text"),
                    "timestamp": child.get("created_at"),
                    "likes": child.get("comment_like_count", 0),
                    "is_reply": True,
                    "parent_id": c.get("pk"),
                }

        # Instagram uses two separate flags:
        # has_more_comments      → more comments in the initial ranked/top view
        # has_more_headload_comments → more comments available via pagination cursor
        # We continue as long as either flag is true AND a cursor exists.
        has_more = payload.get("has_more_comments") or payload.get("has_more_headload_comments")
        min_id = payload.get("next_min_id")
        if not has_more or not min_id:
            break

        if page and page % 5 == 0:
            logger.info("Comment pagination page %d: %d total", page, len(all_comments))

    return list(all_comments.values())


def get_comments_via_graphql(driver, shortcode: str, after: str | None = None) -> dict[str, Any]:
    """Fallback GraphQL comment query executed in-browser."""
    variables = {"shortcode": shortcode, "first": 50, "after": after}
    doc_id = "17852405266163336"  # commonly used comments query; may need rotation
    body = (
        f"variables={json.dumps(variables, separators=(',', ':'))}"
        f"&doc_id={doc_id}"
    )
    url = "https://www.instagram.com/graphql/query"
    return browser_fetch(driver, url, method="POST", body=body)
