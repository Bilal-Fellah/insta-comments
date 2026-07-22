"""Facebook authentication via cookies or login form, plus GraphQL token
extraction (fb_dtsg / lsd / jazoest) needed for internal API calls."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from core.cookies import load_cookies_from_file as _load_cookies
from core.cookies import save_cookies_to_file
from core.human import human_delay, human_type

from .config import ScraperConfig

logger = logging.getLogger(__name__)

HOME_URL = "https://www.facebook.com/"

CHALLENGE_HINTS = (
    "checkpoint",
    "/checkpoint/",
    "two_factor",
    "confirmemail",
    "recover",
    "/help/contact",
    "disabled",
)

# Facebook ships these tokens with slightly varying separators between the
# module name and the {"token":...} object (e.g. `"DTSGInitialData",[],{` vs
# `"DTSGInitialData"],[],{`), so match tolerantly up to the opening brace.
_FB_DTSG_RES = (
    re.compile(r'"DTSGInitialData"[^{]{0,40}\{\s*"token"\s*:\s*"([^"]+)"'),
    re.compile(r'name="fb_dtsg"\s+value="([^"]+)"'),
    re.compile(r'"dtsg"\s*:\s*\{\s*"token"\s*:\s*"([^"]+)"'),
)
_LSD_RES = (
    re.compile(r'"LSD"[^{]{0,40}\{\s*"token"\s*:\s*"([^"]+)"'),
    re.compile(r'name="lsd"\s+value="([^"]+)"'),
)
_SPIN_R_RE = re.compile(r'"__spin_r"\s*:\s*(\d+)')
_REV_RE = re.compile(r'"(?:client_revision|server_revision|__spin_r|rev)"\s*:\s*(\d+)')


def is_challenge_page(driver) -> bool:
    url = (driver.current_url or "").lower()
    if any(h in url for h in CHALLENGE_HINTS):
        return True
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except NoSuchElementException:
        return False
    return any(
        phrase in body_text
        for phrase in (
            "confirm your identity",
            "enter the code",
            "we've temporarily restricted",
            "please confirm it's you",
            "your account has been disabled",
            "complete a security check",
        )
    )


def _cookies_dict(driver) -> dict[str, str]:
    return {c["name"]: c.get("value") for c in driver.get_cookies()}


def is_logged_in(driver) -> bool:
    if is_challenge_page(driver):
        return False
    cookies = _cookies_dict(driver)
    return bool(cookies.get("c_user") and cookies.get("xs"))


def load_cookies_from_file(driver, cookies_file: Path) -> None:
    _load_cookies(driver, cookies_file, HOME_URL)


def dismiss_cookie_banners(driver) -> None:
    xpaths = (
        "//div[@role='button' and contains(., 'Allow all cookies')]",
        "//button[contains(., 'Allow all cookies')]",
        "//div[@role='button' and contains(., 'Decline optional cookies')]",
        "//button[contains(., 'Decline optional cookies')]",
        "//div[@aria-label='Allow all cookies']",
        "//div[@aria-label='Decline optional cookies']",
    )
    for xpath in xpaths:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            btn.click()
            human_delay(1, 2)
            return
        except TimeoutException:
            continue


def _find_login_fields(driver):
    email = driver.find_elements(By.CSS_SELECTOR, "input[name='email'], input#email, input[type='email']")
    pwd = driver.find_elements(By.CSS_SELECTOR, "input[name='pass'], input#pass, input[type='password']")
    return (email[0] if email else None), (pwd[0] if pwd else None)


def login_with_credentials(driver, config: ScraperConfig) -> None:
    if not config.login_username or not config.login_password:
        raise ValueError("login_username and login_password required for login")

    driver.get("https://www.facebook.com/login.php")
    human_delay(3, 6)
    dismiss_cookie_banners(driver)

    wait = WebDriverWait(driver, 40)
    wait.until(lambda d: _find_login_fields(d)[0] is not None)
    email_input, pass_input = _find_login_fields(driver)
    if not email_input or not pass_input:
        raise RuntimeError("Could not locate Facebook login form fields")

    human_type(email_input, config.login_username)
    human_delay(0.5, 1.5)
    human_type(pass_input, config.login_password)
    human_delay(0.8, 2.0)

    try:
        pass_input.submit()
    except Exception:  # noqa: BLE001
        for sel in ("button[name='login']", "button[type='submit']"):
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            if btns:
                btns[0].click()
                break
    human_delay(4, 8)


def extract_tokens(driver) -> dict[str, str]:
    """Pull the GraphQL request tokens from the current page.

    ``jazoest`` is derived from ``fb_dtsg`` (``"2" + sum(ord(c))``) when not
    present verbatim, matching Facebook's own client behaviour.
    """
    source = driver.page_source or ""
    tokens: dict[str, str] = {}

    for rx in _FB_DTSG_RES:
        m = rx.search(source)
        if m:
            tokens["fb_dtsg"] = m.group(1)
            break
    for rx in _LSD_RES:
        m = rx.search(source)
        if m:
            tokens["lsd"] = m.group(1)
            break

    if "fb_dtsg" in tokens:
        tokens["jazoest"] = "2" + str(sum(ord(ch) for ch in tokens["fb_dtsg"]))

    m = _SPIN_R_RE.search(source) or _REV_RE.search(source)
    if m:
        tokens["__spin_r"] = m.group(1)

    cookies = _cookies_dict(driver)
    if cookies.get("c_user"):
        tokens["av"] = cookies["c_user"]

    return tokens


def get_tokens(driver, refresh: bool = False) -> dict[str, str]:
    cached = getattr(driver, "_fb_tokens", None)
    if cached and not refresh:
        return cached
    tokens = extract_tokens(driver)
    if tokens.get("fb_dtsg"):
        driver._fb_tokens = tokens  # type: ignore[attr-defined]
    return tokens


def ensure_session(driver, config: ScraperConfig) -> None:
    driver.get(HOME_URL)
    human_delay(2, 4)
    dismiss_cookie_banners(driver)

    cookies_path = config.resolved_cookies_file()
    if cookies_path and cookies_path.exists():
        logger.info("Loading cookies from %s", cookies_path)
        load_cookies_from_file(driver, cookies_path)

    if is_logged_in(driver):
        logger.info("Authenticated Facebook session detected (reused saved session)")
        get_tokens(driver, refresh=True)
        return

    if config.login_username and config.login_password:
        logger.info("Attempting credential login")
        login_with_credentials(driver, config)
        if is_logged_in(driver):
            logger.info("Login successful")
            get_tokens(driver, refresh=True)
            # Persist the freshly authenticated session so future runs skip login.
            if cookies_path:
                save_cookies_to_file(driver, cookies_path)
            return

    if is_challenge_page(driver):
        logger.warning(
            "Facebook checkpoint detected. Complete verification manually; waiting %ss",
            config.challenge_wait_seconds,
        )
        deadline = time.time() + config.challenge_wait_seconds
        while time.time() < deadline:
            if is_logged_in(driver) and not is_challenge_page(driver):
                logger.info("Checkpoint cleared")
                get_tokens(driver, refresh=True)
                if cookies_path:
                    save_cookies_to_file(driver, cookies_path)
                return
            human_delay(3, 5)
        raise RuntimeError("Checkpoint page not cleared within timeout")

    logger.warning("Proceeding without confirmed login (public data only)")
