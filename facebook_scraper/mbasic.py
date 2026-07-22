"""Best-effort mbasic.facebook.com fallback.

NOTE: Meta retired mbasic.facebook.com for most regions (it now redirects to
www / login), so this is a last resort only. Everything is guarded — if the
lightweight site is unavailable we simply return nothing.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from selenium.webdriver.common.by import By

from core.human import human_delay

logger = logging.getLogger(__name__)


def _to_mbasic(url: str) -> str:
    parsed = urlparse(url)
    return f"https://mbasic.facebook.com{parsed.path}" + (f"?{parsed.query}" if parsed.query else "")


def scrape_mbasic_comments(driver, post_url: str, max_pages: int = 20) -> list[dict[str, Any]]:
    try:
        driver.get(_to_mbasic(post_url))
        human_delay(1.5, 3.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("mbasic navigation failed: %s", exc)
        return []

    cur = (driver.current_url or "").lower()
    if "mbasic.facebook.com" not in cur or "login" in cur:
        logger.info("mbasic unavailable (redirected to %s); skipping", cur[:60])
        return []

    comments: dict[str, dict[str, Any]] = {}
    for _ in range(max_pages):
        for div in driver.find_elements(By.CSS_SELECTOR, "div[id^='comment']"):
            try:
                text = (div.text or "").strip()
                if not text:
                    continue
                username = None
                links = div.find_elements(By.CSS_SELECTOR, "a")
                if links:
                    username = (links[0].text or "").strip() or None
                cid = div.get_attribute("id")
                comments[cid or text[:40]] = {
                    "id": cid,
                    "username": username,
                    "text": text,
                    "timestamp": None,
                    "likes": 0,
                    "is_reply": False,
                    "parent_id": None,
                }
            except Exception:  # noqa: BLE001
                continue

        # Follow the "View more comments" link if present.
        more = None
        for a in driver.find_elements(By.CSS_SELECTOR, "a"):
            if re.search(r"more comments", (a.text or ""), re.I):
                more = a
                break
        if not more:
            break
        try:
            more.click()
            human_delay(1.0, 2.5)
        except Exception:  # noqa: BLE001
            break

    return list(comments.values())
