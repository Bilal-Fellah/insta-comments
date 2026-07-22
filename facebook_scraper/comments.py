"""Facebook comment extraction: embedded JSON -> GraphQL pagination -> DOM."""

from __future__ import annotations

import logging
import re
from typing import Any

from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By

from core.human import human_delay, human_scroll, random_mouse_wander

from .api import (
    get_feedback_comments,
    harvest_all_comment_queries,
    harvest_comment_query,
    paginate_with_query,
)
from .auth import dismiss_cookie_banners, get_tokens
from .config import ScraperConfig
from .interceptor_config import (
    drain_captured_responses,
    extract_comments_from_payload,
    find_feedback_id,
    parse_json_body,
)
from .page_parser import extract_comments_from_page_source, extract_feedback_id_from_source
from .profile import extract_post_metadata_from_page, _post_identifier

logger = logging.getLogger(__name__)

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"


def _ci(term: str) -> str:
    """Case-insensitive contains() for the current node's text."""
    return f"contains(translate(., '{_UPPER}', '{_LOWER}'), '{term}')"


# Buttons that reveal more comments / replies. We match plural 'comments' /
# 'replies' (never the per-comment 'Reply' action, which would open a composer).
_EXPAND_TERMS = (
    "view more comments",
    "view previous comments",
    "more comments",
    "previous comments",
    "view more replies",
    "more replies",
    "replies",
)
EXPAND_XPATHS = [
    f"//*[(@role='button' or self::a)][{_ci(term)}]" for term in _EXPAND_TERMS
]

# Ordering control: switch from "Most relevant" to "All comments" so nothing is
# hidden. Best-effort — ignored if the menu isn't present.
_SORT_OPEN_XPATHS = [
    f"//*[@role='button'][{_ci('most relevant')}]",
    f"//*[@role='button'][{_ci('top comments')}]",
    f"//*[@role='button'][{_ci('sort')}]",
]
_SORT_ALL_XPATHS = [
    f"//*[@role='menuitem'][{_ci('all comments')}]",
    f"//*[@role='menuitemradio'][{_ci('all comments')}]",
    f"//*[{_ci('all comments')} and (@role='menuitem' or @role='menuitemradio')]",
]

_COMMENT_ID_RE = re.compile(r"comment_id=(\d+)")

# Comment scroll container (dialog on permalink, feed otherwise).
COMMENT_CONTAINER_SELECTORS = [
    "div[role='dialog']",
    "div[role='feed']",
]


# Open the sort control and choose an ordering that shows EVERY comment.
# Match on the menu item's first line only (its description mentions "all
# comments" too, which would otherwise mis-select "Newest").
_SWITCH_ALL_JS = r"""
const btns = [...document.querySelectorAll("[role=button]")];
const sort = btns.find(x => /^(most relevant|top comments|newest|all comments)\b/i.test((x.innerText||'').trim()));
if (!sort) return 'no-sort-button';
sort.scrollIntoView({block:'center'}); sort.click();
return 'opened';
"""
_PICK_ALL_JS = r"""
const items = [...document.querySelectorAll("[role=menuitem],[role=menuitemradio],[role=menuitemcheckbox]")];
const firstLine = x => ((x.innerText||'').trim().split('\n')[0] || '').toLowerCase();
const pick = items.find(x => firstLine(x) === 'all comments')
          || items.find(x => firstLine(x) === 'newest');
if (!pick) return null;
pick.click();
return firstLine(pick);
"""

# Click every "View N replies / reply" and "View more/previous comments"
# expander (never the standalone "Reply" action). Returns how many were clicked.
_EXPAND_ALL_JS = r"""
const re = /(^|\bview\s+)(\d+\s+)?repl(y|ies)\b|\bview\s+more\s+repl|\bmore\s+comments?\b|\bview\s+(more|previous)\s+comments?\b|\bprevious\s+comments?\b/i;
let n = 0;
for (const b of document.querySelectorAll("[role=button]")) {
  const t = (b.innerText || '').trim();
  if (!t) continue;
  if (t.toLowerCase() === 'reply') continue;            // per-comment action
  if (re.test(t)) {
    try { b.scrollIntoView({block:'center'}); b.click(); n++; } catch (e) {}
  }
}
return n;
"""


def _switch_to_all_comments(driver) -> bool:
    try:
        if driver.execute_script(_SWITCH_ALL_JS) != "opened":
            return False
        human_delay(0.8, 1.6)
        picked = driver.execute_script(_PICK_ALL_JS)
        if picked:
            human_delay(1.5, 2.8)
            logger.info("Switched comment ordering to '%s'", picked)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _expand_all(driver) -> int:
    try:
        return int(driver.execute_script(_EXPAND_ALL_JS) or 0)
    except Exception:  # noqa: BLE001
        return 0


def _scroll_comment_area(driver) -> None:
    for sel in COMMENT_CONTAINER_SELECTORS:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", els[0])
            return
    human_scroll(driver, direction=1)


def _click_matching(driver, xpaths: list[str], limit: int = 6) -> int:
    """Click matching buttons defensively: a single non-interactable / stale /
    intercepted element must never abort the harvest loop."""
    clicked = 0
    for xpath in xpaths:
        try:
            elements = driver.find_elements(By.XPATH, xpath)[:limit]
        except Exception:  # noqa: BLE001
            continue
        for btn in elements:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                human_delay(0.3, 0.9)
                btn.click()
                clicked += 1
                human_delay(0.6, 1.6)
            except Exception:  # noqa: BLE001
                # Fall back to a JS click; ignore anything that still fails.
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    clicked += 1
                    human_delay(0.6, 1.6)
                except Exception:  # noqa: BLE001
                    continue
    return clicked


# In-page extractor: every comment (top-level or reply) carries a permalink
# with comment_id=. We climb from that link to the comment container and read
# the author name + body text. Runs entirely in the browser for speed.
_DOM_EXTRACT_JS = r"""
const out = [];
const seen = new Set();
document.querySelectorAll("a[href*='comment_id=']").forEach((a) => {
  const m = (a.href || '').match(/comment_id=(\d+)/);
  if (!m) return;
  const id = m[1];
  if (seen.has(id)) return;
  const isReply = /reply_comment_id=/.test(a.href || '');

  // Climb to the smallest ancestor that has both an author link and a text node.
  let el = a.parentElement, container = null, hops = 0;
  while (el && hops < 12) {
    const author = el.querySelector("a[role='link'][href], a[aria-hidden='false'][href]");
    const txt = el.querySelector("div[dir='auto']");
    if (author && txt) { container = el; break; }
    el = el.parentElement; hops++;
  }
  if (!container) return;

  let name = null;
  for (const link of container.querySelectorAll("a[href]")) {
    if ((link.href || '').includes('comment_id=')) continue;
    const t = (link.innerText || '').trim().split('\n')[0];
    if (t) { name = t; break; }
  }
  let text = null;
  for (const d of container.querySelectorAll("div[dir='auto']")) {
    const t = (d.innerText || '').trim();
    if (t && t !== name && t.length > 1) { text = t; break; }
  }
  if (name && text) { seen.add(id); out.push({id, username: name, text, is_reply: isReply}); }
});
return out;
"""


def _extract_dom_comments_js(driver) -> list[dict[str, Any]]:
    try:
        rows = driver.execute_script(_DOM_EXTRACT_JS) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        name = _clean_username((r.get("username") or "").strip())
        text = (r.get("text") or "").strip()
        if not name or not text:
            continue
        out.append(
            {
                "id": str(r.get("id")),
                "username": name,
                "text": text,
                "timestamp": None,
                "likes": 0,
                "is_reply": bool(r.get("is_reply")),
                "parent_id": None,
            }
        )
    return out


def _clean_username(name: str) -> str:
    # Strip a trailing relative timestamp that FB sometimes appends to the label
    # (e.g. "Mahfoudh Lariane 5 hours ago" / "· 5h").
    name = re.split(r"\s*·\s*", name)[0]
    name = re.sub(
        r"\s+\d+\s*(?:y|w|d|h|m|s|hours?|hrs?|mins?|minutes?|days?|weeks?|years?)(?:\s+ago)?$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip()
    return name


def _parse_dom_comments(driver) -> list[dict[str, Any]]:
    """Best-effort DOM parse. Only rows carrying a real ``comment_id`` (from the
    timestamp permalink) are returned, so dedup is exact and we never emit the
    translated/duplicate rows that lack an id."""
    comments: list[dict[str, Any]] = []
    rows = driver.find_elements(By.CSS_SELECTOR, "div[role='article']")
    for row in rows:
        try:
            # Stable id from the comment permalink (timestamp link).
            cid = None
            for a in row.find_elements(By.CSS_SELECTOR, "a[href*='comment_id=']"):
                mm = _COMMENT_ID_RE.search(a.get_attribute("href") or "")
                if mm:
                    cid = mm.group(1)
                    break
            if not cid:
                continue

            aria = (row.get_attribute("aria-label") or "").strip()
            username = None
            m = re.match(r"(?:Comment|Reply) by (.+)", aria)
            if m:
                username = _clean_username(m.group(1).strip())
            if not username:
                for a in row.find_elements(By.CSS_SELECTOR, "a[role='link'][href*='/'], a[href*='facebook.com']"):
                    txt = (a.text or "").strip()
                    if txt:
                        username = _clean_username(txt)
                        break

            text = None
            for div in row.find_elements(By.CSS_SELECTOR, "div[dir='auto']"):
                t = (div.text or "").strip()
                if t and t != username and len(t) > 1:
                    text = t
                    break
            if not username or not text:
                continue

            ts = None
            try:
                ab = row.find_element(By.CSS_SELECTOR, "abbr[data-utime]")
                ts = ab.get_attribute("data-utime")
            except NoSuchElementException:
                pass

            comments.append(
                {
                    "id": cid,
                    "username": username,
                    "text": text,
                    "timestamp": ts,
                    "likes": 0,
                    "is_reply": aria.startswith("Reply"),
                    "parent_id": None,
                }
            )
        except StaleElementReferenceException:
            continue
    return comments


def _resolve_feedback_id(driver) -> str | None:
    fid = extract_feedback_id_from_source(driver.page_source)
    if fid:
        return fid
    for cap in drain_captured_responses(driver):
        payload = parse_json_body(cap.get("body", ""))
        if payload is not None:
            fid = find_feedback_id(payload)
            if fid:
                return fid
    return None


def scrape_post_comments(
    driver,
    config: ScraperConfig,
    post_url: str,
    feedback_id: str | None = None,
    post_meta: dict | None = None,
) -> dict[str, Any]:
    logger.info("Scraping comments for %s", post_url)
    shortcode = _post_identifier(post_url)
    all_comments: dict[str, dict[str, Any]] = {}

    driver.get(post_url)
    human_delay(config.delay_min, config.delay_max)
    dismiss_cookie_banners(driver)

    # 1. Embedded JSON (initial/top comments) + feedback id.
    for c in extract_comments_from_page_source(driver.page_source):
        key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
        all_comments[key] = c

    if not feedback_id:
        feedback_id = _resolve_feedback_id(driver)

    metadata = post_meta or extract_post_metadata_from_page(driver)

    tokens = get_tokens(driver)

    # Harvest helpers. _harvest_network also stashes raw captures (with request
    # bodies) so we can recover Facebook's live comment query below.
    captured: list[dict[str, Any]] = []

    def _merge(comments: list[dict[str, Any]]) -> None:
        for c in comments:
            key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
            if key in all_comments and all_comments[key].get("id"):
                continue
            all_comments[key] = c

    def _harvest_network() -> None:
        caps = drain_captured_responses(driver)
        captured.extend(caps)
        for cap in caps:
            payload = parse_json_body(cap.get("body", ""))
            if payload is not None:
                _merge(extract_comments_from_payload(payload))

    def _harvest_dom() -> None:
        _merge(_extract_dom_comments_js(driver))
        _merge(_parse_dom_comments(driver))
        # FB injects fresh RelayPrefetchedStreamCache <script> tags as replies
        # expand; re-parse the live page source to catch comments the virtualised
        # DOM may have already unmounted.
        try:
            _merge(extract_comments_from_page_source(driver.page_source))
        except Exception:  # noqa: BLE001
            pass

    # Show all comments (not just "Most relevant"), then nudge FB into firing its
    # own comment/reply pagination queries so we can capture and replay them.
    _switch_to_all_comments(driver)
    human_delay(1.0, 2.0)
    _expand_all(driver)
    human_delay(1.5, 2.6)
    _harvest_network()
    _harvest_dom()

    # 2. Self-healing GraphQL (primary bulk path): replay FB's live comment query
    #    — doc_id + variables harvested from its OWN requests — to paginate the
    #    whole thread deterministically. Falls back to hardcoded doc_ids.
    if tokens.get("fb_dtsg"):
        query = harvest_comment_query(captured)
        if query:
            _merge(paginate_with_query(
                driver, tokens, query,
                max_pages=config.max_comment_scroll_rounds,
                delay_range=(config.scroll_pause_min, config.scroll_pause_max),
            ))
            logger.info("After harvested-query pagination: %d unique comments", len(all_comments))
        elif feedback_id:
            _merge(get_feedback_comments(
                driver, feedback_id, tokens,
                max_pages=config.max_comment_scroll_rounds // 3,
                delay_range=(config.scroll_pause_min, config.scroll_pause_max),
            ))
    else:
        logger.warning("No fb_dtsg token available; skipping GraphQL comment path")

    random_mouse_wander(driver, config.window_width, config.window_height)

    # Most of this post's comments are REPLIES behind "View N replies" — expand
    # every thread (each expansion can reveal more), harvesting the DOM and any
    # GraphQL FB fetches after each pass. Stop once no expander fires AND the
    # count stops growing for a couple of rounds.
    idle_rounds = 0
    for round_idx in range(config.max_comment_scroll_rounds):
        before = len(all_comments)

        clicked = _expand_all(driver)
        # Replies render asynchronously — give them a beat to appear.
        human_delay(1.2, 2.4)
        _scroll_comment_area(driver)
        human_delay(config.scroll_pause_min, config.scroll_pause_max)
        _harvest_network()
        _harvest_dom()
        random_mouse_wander(driver, config.window_width, config.window_height)

        grew = len(all_comments) > before
        # Only a non-growing, nothing-to-click round counts as idle, so we don't
        # spin forever on buttons that never expand.
        idle_rounds = 0 if grew else idle_rounds + 1
        if idle_rounds >= config.max_idle_rounds and clicked == 0:
            logger.info("Comments exhausted after %d idle rounds; stopping", idle_rounds)
            break
        if idle_rounds >= config.max_idle_rounds * 2:
            logger.info("Stopping: %d idle rounds (expanders unproductive)", idle_rounds)
            break
        if round_idx % 3 == 0:
            logger.info(
                "Round %d: %d unique comments (clicked %d expanders this round)",
                round_idx, len(all_comments), clicked,
            )

    # Final sweep: replay every distinct comment/reply query FB fired during the
    # session, paginating each thread to the end via its harvested cursor.
    _harvest_network()
    if tokens.get("fb_dtsg"):
        for q in harvest_all_comment_queries(captured):
            try:
                _merge(paginate_with_query(
                    driver, tokens, q,
                    max_pages=config.max_comment_scroll_rounds,
                    delay_range=(config.scroll_pause_min, config.scroll_pause_max),
                ))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Query replay failed: %s", exc)
        logger.info("After full query-replay sweep: %d unique comments", len(all_comments))

    comments_list = list(all_comments.values())
    logger.info("Extracted %d comments from %s", len(comments_list), post_url)

    return {
        "post_url": post_url,
        "feedback_id": feedback_id,
        "metadata": metadata,
        "comment_count_extracted": len(comments_list),
        "comments": comments_list,
    }
