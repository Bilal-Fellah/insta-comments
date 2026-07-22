"""Execute authenticated in-browser fetch() calls.

Runs a fetch inside the page context via ``execute_async_script`` so the
request carries the live session cookies (``credentials: 'include'``) and any
platform CSRF token. This is how we reach internal REST/GraphQL endpoints
without reconstructing auth in Python.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# arguments: url, method, body, headersJson ; last arg is the async callback.
_FETCH_JS = """
const [url, method, body, headersJson] = arguments;
const cb = arguments[arguments.length - 1];
let headers = {};
try { headers = JSON.parse(headersJson || '{}'); } catch (e) {}
if (!headers['Referer']) headers['Referer'] = window.location.href;
fetch(url, {
  method: method || 'GET',
  headers,
  credentials: 'include',
  body: body || undefined,
})
  .then(async (r) => ({ status: r.status, text: await r.text(), contentType: r.headers.get('content-type') || '' }))
  .then(cb)
  .catch((e) => cb({ status: 0, text: String(e), contentType: '' }));
"""


def _strip_xssi(text: str) -> str:
    text = text.lstrip()
    for prefix in ("for (;;);", "for(;;);", ")]}'"):
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def browser_fetch(
    driver,
    url: str,
    method: str = "GET",
    body: str | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Perform an in-page fetch and return ``{status, text, json, content_type}``.

    ``headers`` are merged as-is (platform passes app-id / CSRF / GraphQL
    headers). The JSON body is parsed when the response looks like JSON.
    """
    headers_json = json.dumps(headers or {})
    result = driver.execute_async_script(_FETCH_JS, url, method, body, headers_json)
    status = result.get("status", 0)
    text = result.get("text", "")
    content_type = result.get("contentType", "")
    if status >= 400 or (text.startswith("<!DOCTYPE") and "json" not in content_type.lower()):
        logger.warning("Fetch %s returned HTTP %s (non-JSON): %s", url, status, text[:120])

    payload = None
    if text and not text.lstrip().startswith("<!DOCTYPE"):
        try:
            payload = json.loads(_strip_xssi(text))
        except json.JSONDecodeError:
            payload = None
    return {"status": status, "text": text, "json": payload, "content_type": content_type}
