"""Generic network-response capture framework.

Injects a page-context interceptor that hooks ``fetch`` and ``XMLHttpRequest``,
buffering responses whose URL contains any of a platform-supplied set of
substrings. The buffer is drained from Python and parsed. Field mapping of the
captured payloads (e.g. pulling comments out) is platform-specific and lives in
each platform package.
"""

from __future__ import annotations

import json
from typing import Any, Callable

# Template with two placeholders filled by build_interceptor_js:
#   {GLOBAL}   - window global name (flag + buffer suffix)
#   {MATCHERS} - JS array literal of substrings to match against request URLs
_INTERCEPTOR_TEMPLATE = """
(() => {
  const FLAG = '__%(GLOBAL)sInterceptor';
  const BUF = '__%(GLOBAL)sCaptured';
  if (window[FLAG]) return;
  window[FLAG] = true;
  window[BUF] = window[BUF] || [];

  const MATCHERS = %(MATCHERS)s;

  const pushCapture = (url, body, status, method, reqBody) => {
    try {
      window[BUF].push({
        url: String(url),
        body: typeof body === 'string' ? body : JSON.stringify(body),
        reqBody: typeof reqBody === 'string' ? reqBody : (reqBody ? JSON.stringify(reqBody) : ''),
        status: status || 200,
        method: method || 'GET',
        ts: Date.now()
      });
      if (window[BUF].length > 500) window[BUF].shift();
    } catch (e) {}
  };

  const shouldCapture = (url) => {
    if (!url) return false;
    const u = String(url);
    for (let i = 0; i < MATCHERS.length; i++) {
      if (u.indexOf(MATCHERS[i]) !== -1) return true;
    }
    return false;
  };

  const origFetch = window.fetch;
  window.fetch = async function(input, init) {
    const resp = await origFetch.apply(this, arguments);
    try {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      if (shouldCapture(url)) {
        const clone = resp.clone();
        const text = await clone.text();
        let rb = (init && init.body) || '';
        pushCapture(url, text, resp.status, (init && init.method) || 'GET', rb);
      }
    } catch (e) {}
    return resp;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__capMethod = method;
    this.__capUrl = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(body) {
    this.__capReqBody = body;
    this.addEventListener('load', function() {
      try {
        if (shouldCapture(this.__capUrl)) {
          pushCapture(this.__capUrl, this.responseText, this.status, this.__capMethod, this.__capReqBody);
        }
      } catch (e) {}
    });
    return origSend.apply(this, arguments);
  };
})();
"""


def build_interceptor_js(match_substrings: list[str], global_name: str) -> str:
    """Return interceptor JS for a platform.

    ``global_name`` namespaces the window globals so platforms don't collide;
    ``match_substrings`` are URL fragments that trigger capture.
    """
    return _INTERCEPTOR_TEMPLATE % {
        "GLOBAL": global_name,
        "MATCHERS": json.dumps(match_substrings),
    }


def inject_interceptor(driver, interceptor_js: str) -> None:
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": interceptor_js})


def drain_captured_responses(driver, global_name: str) -> list[dict[str, Any]]:
    raw = driver.execute_script(
        """
        const key = '__' + arguments[0] + 'Captured';
        const items = window[key] || [];
        window[key] = [];
        return items;
        """,
        global_name,
    )
    return raw or []


def parse_json_body(body: str) -> Any | None:
    """Parse a captured response body, stripping XSSI prefixes used by both
    Instagram and Facebook (``for (;;);``)."""
    if not body:
        return None
    body = body.strip()
    if body.startswith("for (;;);"):
        body = body[len("for (;;);") :]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def walk_collect(node: Any, visit: Callable[[dict], None]) -> None:
    """Recursively walk a JSON structure, calling ``visit`` on every dict node.

    Platform extractors pass a ``visit`` that inspects the dict and appends any
    matched records to their own accumulator.
    """
    if isinstance(node, dict):
        visit(node)
        for value in node.values():
            walk_collect(value, visit)
    elif isinstance(node, list):
        for item in node:
            walk_collect(item, visit)
