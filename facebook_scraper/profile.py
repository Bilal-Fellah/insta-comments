"""Facebook profile/page navigation and post discovery."""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.human import human_delay, human_scroll, random_mouse_wander
from core.models import PostRef

from .auth import dismiss_cookie_banners
from .config import ScraperConfig
from .interceptor_config import drain_captured_responses, parse_json_body

logger = logging.getLogger(__name__)

FB_HOST = "www.facebook.com"

# Recognised permalink shapes. `/share/{p,v,r,g}/<token>` are short share links
# that redirect to a canonical post — the browser follows the redirect on
# driver.get(), so we accept them as-is.
_POST_PATH_RE = re.compile(
    r"/(?:posts|videos|reel|watch|photo|permalink|story|share)\b"
    r"|/permalink\.php|/story\.php|/photo\.php|/watch/?"
)
_PFBID_RE = re.compile(r"(pfbid[0-9A-Za-z]+)")
_ID_IN_PATH_RE = re.compile(r"/(?:videos|reel)/(\d+)")
_SHARE_RE = re.compile(r"/share/[a-z]/([^/?#]+)")

_SKIP_SEGMENTS = frozenset(
    {"", "home.php", "profile.php", "login", "login.php", "help", "policies", "settings"}
)


def _clean_host(parsed) -> str:
    return FB_HOST


def normalize_profile_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    if parsed.path.startswith("/profile.php"):
        qs = parse_qs(parsed.query)
        pid = (qs.get("id") or [""])[0]
        if pid:
            return f"https://{FB_HOST}/profile.php?id={pid}"
    if not path or path == "/":
        raise ValueError(f"Invalid Facebook profile URL: {url}")
    username = path.strip("/").split("/")[0]
    return f"https://{FB_HOST}/{username}/"


def extract_page_name(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.path.startswith("/profile.php"):
        qs = parse_qs(parsed.query)
        return (qs.get("id") or ["profile"])[0]
    seg = parsed.path.strip("/").split("/")[0]
    return seg or "facebook"


def _post_identifier(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("story_fbid", "fbid", "v"):
        if qs.get(key):
            return qs[key][0]
    m = _PFBID_RE.search(url)
    if m:
        return m.group(1)
    m = _ID_IN_PATH_RE.search(parsed.path)
    if m:
        return m.group(1)
    m = _SHARE_RE.search(parsed.path)
    if m:
        return m.group(1)
    seg = [s for s in parsed.path.strip("/").split("/") if s]
    return seg[-1] if seg else "post"


def normalize_post_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.path or not _POST_PATH_RE.search(parsed.path):
        raise ValueError(
            f"Invalid Facebook post URL (expected /posts/, /permalink.php, /videos/, /reel/, "
            f"/watch, /photo): {url}"
        )
    qs = parse_qs(parsed.query)

    if parsed.path.startswith(("/permalink.php", "/story.php")):
        story = (qs.get("story_fbid") or [""])[0]
        owner = (qs.get("id") or [""])[0]
        if story and owner:
            return f"https://{FB_HOST}/permalink.php?story_fbid={story}&id={owner}"
    if parsed.path.startswith("/watch"):
        v = (qs.get("v") or [""])[0]
        if v:
            return f"https://{FB_HOST}/watch/?v={v}"
    if parsed.path.startswith("/photo"):
        fbid = (qs.get("fbid") or [""])[0]
        if fbid:
            return f"https://{FB_HOST}/photo.php?fbid={fbid}"

    path = parsed.path.rstrip("/")
    return f"https://{FB_HOST}{path}/"


def is_valid_post_url(url: str) -> bool:
    try:
        normalize_post_url(url)
        return True
    except ValueError:
        return False


def posts_from_urls(urls: list[str]) -> list[PostRef]:
    refs: list[PostRef] = []
    seen: set[str] = set()
    for raw in urls:
        url = normalize_post_url(raw)
        ident = _post_identifier(url)
        if ident in seen:
            continue
        seen.add(ident)
        refs.append(PostRef(url=url, shortcode=ident))
    return refs


# --- discovery ---------------------------------------------------------------

_HREF_SELECTORS = (
    "a[href*='/posts/']",
    "a[href*='story_fbid=']",
    "a[href*='/videos/']",
    "a[href*='/reel/']",
    "a[href*='/watch/?v=']",
    "a[href*='permalink.php']",
)

_PERMALINK_IN_TEXT_RE = re.compile(
    r'(?:https?://[^"\\ ]*?)?(?:/[^"\\ ]*/posts/pfbid[0-9A-Za-z]+'
    r'|/permalink\.php\?story_fbid=\d+&id=\d+'
    r'|/reel/\d+|/videos/\d+)'
)


def _add_ref(url: str, seen: set[str], posts: list[PostRef]) -> None:
    try:
        norm = normalize_post_url(url)
    except ValueError:
        return
    ident = _post_identifier(norm)
    if ident in seen:
        return
    seen.add(ident)
    posts.append(PostRef(url=norm, shortcode=ident))


def collect_recent_posts(driver, config: ScraperConfig) -> list[PostRef]:
    profile_url = normalize_profile_url(config.profile_url)
    logger.info("Opening page %s", profile_url)
    driver.get(profile_url)
    human_delay(config.delay_min, config.delay_max)
    dismiss_cookie_banners(driver)

    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    except TimeoutException:
        logger.warning("Page content did not load in time")

    random_mouse_wander(driver, config.window_width, config.window_height)

    seen: set[str] = set()
    posts: list[PostRef] = []
    idle_rounds = 0

    while len(posts) < config.post_limit and idle_rounds < config.max_idle_rounds:
        before = len(posts)

        # Network captures (comet feed responses embed story permalinks).
        for cap in drain_captured_responses(driver):
            body = cap.get("body", "")
            payload = parse_json_body(body)
            if payload is None:
                for m in _PERMALINK_IN_TEXT_RE.findall(body):
                    _add_ref(m, seen, posts)
                    if len(posts) >= config.post_limit:
                        break
                continue
            # Walk stringified payload for permalinks too (cheap + robust).
            for m in _PERMALINK_IN_TEXT_RE.findall(json_dumps_safe(payload)):
                _add_ref(m, seen, posts)
                if len(posts) >= config.post_limit:
                    break

        # DOM anchors.
        for sel in _HREF_SELECTORS:
            for a in driver.find_elements(By.CSS_SELECTOR, sel):
                href = a.get_attribute("href") or ""
                if href:
                    _add_ref(href, seen, posts)
                    if len(posts) >= config.post_limit:
                        break
            if len(posts) >= config.post_limit:
                break

        # Embedded permalinks in raw HTML.
        if len(posts) < config.post_limit:
            for m in _PERMALINK_IN_TEXT_RE.findall(driver.page_source):
                _add_ref(m, seen, posts)
                if len(posts) >= config.post_limit:
                    break

        if len(posts) >= config.post_limit:
            break

        idle_rounds = idle_rounds + 1 if len(posts) == before else 0
        human_scroll(driver)
        human_delay(config.scroll_pause_min, config.scroll_pause_max)

    logger.info("Collected %d post URLs", len(posts))
    return posts[: config.post_limit]


def json_dumps_safe(payload) -> str:
    import json

    try:
        return json.dumps(payload)
    except (TypeError, ValueError):
        return ""


def extract_post_metadata_from_page(driver) -> dict:
    meta: dict = {"caption": None, "like_count": None, "comment_count": None, "timestamp": None}
    try:
        time_el = driver.find_element(By.CSS_SELECTOR, "abbr[data-utime], time")
        meta["timestamp"] = time_el.get_attribute("data-utime") or time_el.get_attribute("datetime")
    except NoSuchElementException:
        pass
    return meta
