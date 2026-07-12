"""Comment extraction via network capture + DOM interaction."""

from __future__ import annotations

import logging
import re
from typing import Any

from selenium.common.exceptions import ElementClickInterceptedException, NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By

from .auth import dismiss_cookie_banners
from .api import get_media_comments
from .config import ScraperConfig
from .human import human_delay, human_scroll, random_mouse_wander
from .interceptors import drain_captured_responses, extract_comments_from_payload, parse_json_body
from .page_parser import extract_comments_from_page_source
from .profile import extract_post_metadata_from_page

logger = logging.getLogger(__name__)

COMMENT_CONTAINER_SELECTORS = [
    "div[role='dialog'] ul",
    "article ul",
    "div[style*='overflow'] ul",
]

LOAD_MORE_XPATHS = [
    "//button[contains(translate(., 'LOAD MORE', 'load more'), 'load more')]",
    "//span[contains(translate(., 'VIEW MORE COMMENTS', 'view more comments'), 'view more')]/ancestor::button",
    "//button[contains(., 'View more comments')]",
    "//button[contains(., 'Load more comments')]",
]

REPLY_XPATHS = [
    "//span[contains(., 'View replies')]",
    "//span[contains(., 'View all') and contains(., 'repl')]",
    "//button[contains(., 'View replies')]",
    "//button[contains(., 'View all') and contains(., 'repl')]",
]


def _find_comment_container(driver):
    for sel in COMMENT_CONTAINER_SELECTORS:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        if els:
            return els[0]
    return None


def _click_matching(driver, xpaths: list[str], limit: int = 5) -> int:
    clicked = 0
    for xpath in xpaths:
        buttons = driver.find_elements(By.XPATH, xpath)
        for btn in buttons[:limit]:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                human_delay(0.4, 1.2)
                btn.click()
                clicked += 1
                human_delay(0.8, 2.0)
            except (ElementClickInterceptedException, StaleElementReferenceException, NoSuchElementException):
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    clicked += 1
                    human_delay(0.8, 2.0)
                except Exception:  # noqa: BLE001
                    continue
    return clicked


def _parse_dom_comments(driver) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    rows = driver.find_elements(
        By.XPATH,
        "//ul//ul//li | //div[@role='dialog']//ul/li",
    )
    for row in rows:
        try:
            username = None
            for a in row.find_elements(By.CSS_SELECTOR, "a[href*='/']"):
                href = a.get_attribute("href") or ""
                if "/p/" in href or "/reel/" in href:
                    continue
                m = re.search(r"instagram.com/([^/?#]+)", href)
                if m and m.group(1) not in {"explore", "accounts", "direct", "stories"}:
                    username = m.group(1)
                    break
            spans = row.find_elements(By.CSS_SELECTOR, "span")
            text = None
            for sp in spans:
                t = (sp.text or "").strip()
                if t and (not username or t != username) and len(t) > 1:
                    text = t
                    break
            if not username or not text:
                continue
            ts = None
            try:
                ts = row.find_element(By.TAG_NAME, "time").get_attribute("datetime")
            except NoSuchElementException:
                pass
            likes = 0
            for sp in spans:
                t = (sp.text or "").lower()
                if t.isdigit():
                    likes = int(t)
            comments.append(
                {
                    "username": username,
                    "text": text,
                    "timestamp": ts,
                    "likes": likes,
                    "is_reply": False,
                    "parent_id": None,
                }
            )
        except StaleElementReferenceException:
            continue
    return comments


def scrape_post_comments(
    driver,
    config: ScraperConfig,
    post_url: str,
    media_id: str | None = None,
    post_meta: dict | None = None,
) -> dict[str, Any]:
    logger.info("Scraping comments for %s", post_url)
    shortcode = post_url.rstrip("/").split("/")[-1]
    all_comments: dict[str, dict[str, Any]] = {}

    driver.get(post_url)
    human_delay(config.delay_min, config.delay_max)
    dismiss_cookie_banners(driver)

    # Embedded JSON in post page (works logged-out for preview comments)
    for c in extract_comments_from_page_source(driver.page_source):
        key = c.get("id") or f"{c.get('username')}:{c.get('text')}"
        all_comments[key] = c

    if not media_id:
        for cap in drain_captured_responses(driver):
            payload = parse_json_body(cap.get("body", ""))
            if isinstance(payload, dict):
                items = payload.get("items") or []
                if items and items[0].get("pk"):
                    media_id = str(items[0]["pk"])
                    break
                media = (payload.get("data") or {}).get("shortcode_media") or {}
                if media.get("id"):
                    media_id = str(media["id"])
                    break
        if not media_id:
            m = re.search(r'"media_id":"(\d+)"', driver.page_source)
            if m:
                media_id = m.group(1)

    metadata = post_meta or extract_post_metadata_from_page(driver)

    # REST comments API with pagination (requires authenticated session for full threads)
    if media_id:
        api_comments = get_media_comments(
            driver,
            media_id,
            shortcode,
            max_pages=config.max_comment_scroll_rounds // 3,
            delay_range=(config.scroll_pause_min, config.scroll_pause_max),
        )
        for c in api_comments:
            key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
            all_comments[key] = c
        logger.info("API extracted %d total unique comments so far", len(all_comments))

    expected_count = (metadata or {}).get("comment_count") or 0
    if all_comments and expected_count > 0 and len(all_comments) >= expected_count:
        # We already have at least as many comments as the post reports — skip DOM scrolling.
        comments_list = list(all_comments.values())
        return {
            "post_url": post_url,
            "shortcode": shortcode,
            "media_id": media_id,
            "metadata": metadata,
            "comment_count_extracted": len(comments_list),
            "comments": comments_list,
        }

    # Supplement with DOM + network interception when API/login limits apply
    random_mouse_wander(driver, config.window_width, config.window_height)

    idle_rounds = 0
    for round_idx in range(config.max_comment_scroll_rounds):
        # Click load-more / reply expanders
        _click_matching(driver, LOAD_MORE_XPATHS, limit=3)
        _click_matching(driver, REPLY_XPATHS, limit=8)

        # Capture network payloads
        for cap in drain_captured_responses(driver):
            payload = parse_json_body(cap.get("body", ""))
            if payload:
                for c in extract_comments_from_payload(payload):
                    key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
                    all_comments[key] = c

        # DOM fallback
        for c in _parse_dom_comments(driver):
            key = f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
            all_comments.setdefault(key, c)

        before = len(all_comments)

        container = _find_comment_container(driver)
        if container:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
        else:
            human_scroll(driver, direction=1)

        human_delay(config.scroll_pause_min, config.scroll_pause_max)
        random_mouse_wander(driver, config.window_width, config.window_height)

        # Second pass after scroll
        for cap in drain_captured_responses(driver):
            payload = parse_json_body(cap.get("body", ""))
            if payload:
                for c in extract_comments_from_payload(payload):
                    key = c.get("id") or f"{c.get('username')}:{c.get('text')}:{c.get('timestamp')}"
                    all_comments[key] = c

        if len(all_comments) == before:
            idle_rounds += 1
        else:
            idle_rounds = 0

        if idle_rounds >= config.max_idle_rounds:
            logger.info("No new comments after %d idle rounds; stopping", idle_rounds)
            break

        if round_idx and round_idx % 10 == 0:
            logger.info("Round %d: %d unique comments collected", round_idx, len(all_comments))

    comments_list = list(all_comments.values())
    logger.info("Extracted %d comments from %s", len(comments_list), post_url)

    return {
        "post_url": post_url,
        "metadata": metadata,
        "comment_count_extracted": len(comments_list),
        "comments": comments_list,
    }
