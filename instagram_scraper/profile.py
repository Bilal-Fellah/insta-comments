"""Profile navigation and post discovery."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.human import human_delay, human_scroll, random_mouse_wander

from .api import get_profile_timeline
from .auth import dismiss_cookie_banners
from .config import ScraperConfig
from .interceptor_config import drain_captured_responses, parse_json_body
from core.models import PostRef

logger = logging.getLogger(__name__)


def normalize_profile_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    if not path or path == "/":
        raise ValueError(f"Invalid profile URL: {url}")
    username = path.split("/")[-1]
    return f"https://www.instagram.com/{username}/"


def extract_username(url: str) -> str:
    return urlparse(normalize_profile_url(url)).path.strip("/").split("/")[0]


POST_PATH_RE = re.compile(r"/(?:p|reel|tv)/([^/?#]+)/?")


def normalize_post_url(url: str) -> str:
    parsed = urlparse(url.strip())
    m = POST_PATH_RE.search(parsed.path)
    if not m:
        raise ValueError(f"Invalid Instagram post URL (expected /p/, /reel/, or /tv/): {url}")
    shortcode = m.group(1)
    if not is_valid_shortcode(shortcode):
        raise ValueError(f"Invalid shortcode in post URL: {url}")
    kind = "p"
    if "/reel/" in parsed.path:
        kind = "reel"
    elif "/tv/" in parsed.path:
        kind = "tv"
    return f"https://www.instagram.com/{kind}/{shortcode}/"


def extract_shortcode_from_post_url(url: str) -> str:
    return normalize_post_url(url).rstrip("/").split("/")[-1]


def posts_from_urls(urls: list[str]) -> list[PostRef]:
    refs: list[PostRef] = []
    seen: set[str] = set()
    for raw in urls:
        url = normalize_post_url(raw)
        shortcode = extract_shortcode_from_post_url(url)
        if shortcode in seen:
            continue
        seen.add(shortcode)
        refs.append(PostRef(url=url, shortcode=shortcode))
    return refs


INVALID_SHORTCODES = frozenset(
    {
        "explore",
        "accounts",
        "en_us",
        "en_gb",
        "fr_fr",
        "de_de",
        "es_es",
        "reels",
        "stories",
        "direct",
        "about",
        "blog",
        "instagram",
    }
)


def is_valid_shortcode(code: str) -> bool:
    if not code or len(code) < 5 or len(code) > 40:
        return False
    if code.lower() in INVALID_SHORTCODES:
        return False
    if "_" in code and code.islower():
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", code))


def _extract_shortcodes_from_source(source: str) -> list[str]:
    patterns = [
        r'"shortcode":"([A-Za-z0-9_-]+)"',
        r'"/p/([A-Za-z0-9_-]+)/"',
        r'"/reel/([A-Za-z0-9_-]+)/"',
        r'"code":"([A-Za-z0-9_-]+)"',
    ]
    found: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        for code in re.findall(pat, source):
            if code in seen or not is_valid_shortcode(code):
                continue
            seen.add(code)
            found.append(code)
    return found


def _extract_shortcodes_from_payload(payload) -> list[str]:
    codes: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            sc = node.get("shortcode") or node.get("code")
            if sc and isinstance(sc, str) and is_valid_shortcode(sc):
                codes.append(sc)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    dedup: list[str] = []
    seen: set[str] = set()
    for c in codes:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    return dedup


def collect_recent_posts(driver, config: ScraperConfig) -> list[PostRef]:
    profile_url = normalize_profile_url(config.profile_url)
    username = extract_username(config.profile_url)
    logger.info("Opening profile %s", profile_url)
    driver.get(profile_url)
    human_delay(config.delay_min, config.delay_max)
    dismiss_cookie_banners(driver)

    # Primary: internal REST API (most reliable in 2026)
    api_posts = get_profile_timeline(
        driver,
        username,
        delay_range=(config.scroll_pause_min, config.scroll_pause_max),
    )
    if api_posts:
        refs = [
            PostRef(
                url=p["url"],
                shortcode=p["shortcode"],
                media_id=str(p["id"]) if p.get("id") else None,
                metadata={
                    "caption": p.get("caption"),
                    "timestamp": p.get("timestamp"),
                    "like_count": p.get("like_count"),
                    "comment_count": p.get("comment_count"),
                },
            )
            for p in api_posts[: config.post_limit]
        ]
        logger.info("Collected %d post URLs via API", len(refs))
        return refs

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "main"))
        )
    except TimeoutException:
        logger.warning("Profile main content did not load in time")

    human_delay(2, 4)
    random_mouse_wander(driver, config.window_width, config.window_height)

    seen: set[str] = set()
    posts: list[PostRef] = []
    idle_rounds = 0

    while len(posts) < config.post_limit and idle_rounds < config.max_idle_rounds:
        before = len(posts)

        # Network captures
        for cap in drain_captured_responses(driver):
            payload = parse_json_body(cap.get("body", ""))
            if payload:
                for code in _extract_shortcodes_from_payload(payload):
                    if code in seen or not is_valid_shortcode(code):
                        continue
                    seen.add(code)
                    posts.append(
                        PostRef(
                            url=f"https://www.instagram.com/p/{code}/",
                            shortcode=code,
                        )
                    )
                    if len(posts) >= config.post_limit:
                        break

        # DOM anchors
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='/p/'], a[href*='/reel/']")
        for a in anchors:
            href = a.get_attribute("href") or ""
            m = re.search(r"/(?:p|reel)/([^/?#]+)", href)
            if not m:
                continue
            shortcode = m.group(1)
            if shortcode in seen or not is_valid_shortcode(shortcode):
                continue
            seen.add(shortcode)
            posts.append(PostRef(url=href.split("?")[0], shortcode=shortcode))
            if len(posts) >= config.post_limit:
                break

        # Embedded JSON in HTML
        if len(posts) < config.post_limit:
            for code in _extract_shortcodes_from_source(driver.page_source):
                if code in seen:
                    continue
                seen.add(code)
                posts.append(
                    PostRef(
                        url=f"https://www.instagram.com/p/{code}/",
                        shortcode=code,
                    )
                )
                if len(posts) >= config.post_limit:
                    break

        if len(posts) >= config.post_limit:
            break

        if len(posts) == before:
            idle_rounds += 1
        else:
            idle_rounds = 0

        human_scroll(driver)
        human_delay(config.scroll_pause_min, config.scroll_pause_max)

    logger.info("Collected %d post URLs", len(posts))
    return posts[: config.post_limit]


def extract_post_metadata_from_page(driver) -> dict:
    meta: dict = {"caption": None, "like_count": None, "comment_count": None, "timestamp": None}
    try:
        caption_el = driver.find_element(By.CSS_SELECTOR, "h1, span[class*='Caption'], div[class*='Caption']")
        meta["caption"] = caption_el.text.strip() or None
    except NoSuchElementException:
        pass

    try:
        time_el = driver.find_element(By.TAG_NAME, "time")
        meta["timestamp"] = time_el.get_attribute("datetime")
    except NoSuchElementException:
        pass

    for el in driver.find_elements(By.XPATH, "//section//span | //a/span"):
        txt = (el.text or "").strip().lower()
        if " likes" in txt or txt.endswith("like"):
            digits = re.sub(r"[^\d]", "", txt.split()[0])
            if digits:
                meta["like_count"] = int(digits)
        if " comments" in txt or txt.endswith("comment"):
            digits = re.sub(r"[^\d]", "", txt.split()[0])
            if digits:
                meta["comment_count"] = int(digits)

    return meta
