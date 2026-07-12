"""Network interception for Instagram GraphQL / REST responses."""

from __future__ import annotations

import json
import re
from typing import Any

INTERCEPTOR_JS = """
(() => {
  if (window.__igScraperInterceptor) return;
  window.__igScraperInterceptor = true;
  window.__igCapturedResponses = window.__igCapturedResponses || [];

  const pushCapture = (url, body, status, method) => {
    try {
      window.__igCapturedResponses.push({
        url: String(url),
        body: typeof body === 'string' ? body : JSON.stringify(body),
        status: status || 200,
        method: method || 'GET',
        ts: Date.now()
      });
      if (window.__igCapturedResponses.length > 500) {
        window.__igCapturedResponses.shift();
      }
    } catch (e) {}
  };

  const shouldCapture = (url) => {
    if (!url) return false;
    const u = String(url);
    return u.includes('/graphql') || u.includes('/api/v1/') || u.includes('comments') || u.includes('comment');
  };

  const origFetch = window.fetch;
  window.fetch = async function(input, init) {
    const resp = await origFetch.apply(this, arguments);
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (shouldCapture(url)) {
        const clone = resp.clone();
        const text = await clone.text();
        pushCapture(url, text, resp.status, (init && init.method) || 'GET');
      }
    } catch (e) {}
    return resp;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__igMethod = method;
    this.__igUrl = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function() {
    this.addEventListener('load', function() {
      try {
        if (shouldCapture(this.__igUrl)) {
          pushCapture(this.__igUrl, this.responseText, this.status, this.__igMethod);
        }
      } catch (e) {}
    });
    return origSend.apply(this, arguments);
  };
})();
"""


def inject_interceptor(driver) -> None:
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": INTERCEPTOR_JS})


def drain_captured_responses(driver) -> list[dict[str, Any]]:
    raw = driver.execute_script(
        """
        const items = window.__igCapturedResponses || [];
        window.__igCapturedResponses = [];
        return items;
        """
    )
    return raw or []


def parse_json_body(body: str) -> Any | None:
    if not body:
        return None
    body = body.strip()
    if body.startswith("for (;;);"):
        body = body[len("for (;;);") :]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _walk_comments(node: Any, found: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        username = None
        text = None
        created_at = None
        like_count = None
        pk = node.get("pk") or node.get("id")
        user = node.get("user") or node.get("owner") or {}

        if isinstance(user, dict):
            username = user.get("username") or user.get("full_name")
        if "text" in node and username:
            text = node.get("text")
            created_at = node.get("created_at") or node.get("created_at_utc")
            like_count = node.get("comment_like_count") or node.get("like_count") or 0
            found.append(
                {
                    "id": str(pk) if pk else None,
                    "username": username,
                    "text": text,
                    "timestamp": created_at,
                    "likes": like_count,
                    "is_reply": bool(node.get("parent_comment_id") or node.get("replied_to_comment_id")),
                    "parent_id": node.get("parent_comment_id") or node.get("replied_to_comment_id"),
                }
            )

        for key in ("child_comments", "preview_child_comments", "edge_threaded_comments", "replies"):
            child = node.get(key)
            if isinstance(child, dict) and "edges" in child:
                for edge in child.get("edges", []):
                    _walk_comments(edge.get("node"), found)
            elif isinstance(child, list):
                for item in child:
                    _walk_comments(item, found)

        for value in node.values():
            _walk_comments(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_comments(item, found)


def extract_comments_from_payload(payload: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    _walk_comments(payload, found)
    dedup: dict[str, dict[str, Any]] = {}
    for c in found:
        key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
        dedup[key] = c
    return list(dedup.values())


def extract_shortcode_from_url(url: str) -> str | None:
    m = re.search(r"/(?:p|reel|tv)/([^/?#]+)", url)
    return m.group(1) if m else None
