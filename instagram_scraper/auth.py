"""Authentication via cookies or login form."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import ScraperConfig
from .human import human_delay, human_type

logger = logging.getLogger(__name__)

CHALLENGE_HINTS = (
    "challenge",
    "checkpoint",
    "consent",
    "two_factor",
    "login_required",
    "accounts/suspended",
)


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
            "help us confirm",
            "suspicious login",
            "verify it's you",
            "enter the code",
            "captcha",
        )
    )


def is_logged_in(driver) -> bool:
    if is_challenge_page(driver):
        return False
    cookies = {c["name"]: c.get("value") for c in driver.get_cookies()}
    return bool(cookies.get("sessionid") and cookies.get("ds_user_id"))


def load_cookies_from_file(driver, cookies_file: Path) -> None:
    with cookies_file.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    if isinstance(data, dict) and "cookies" in data:
        cookies = data["cookies"]
    elif isinstance(data, list):
        cookies = data
    else:
        raise ValueError("Unsupported cookie file format. Export JSON array or {cookies: [...]}.")

    driver.get("https://www.instagram.com/")
    human_delay(2, 4)

    for cookie in cookies:
        c = {k: cookie[k] for k in cookie if k in {"name", "value", "domain", "path", "expiry", "secure", "httpOnly", "sameSite"}}
        if "name" not in c or "value" not in c:
            continue
        if c.get("domain", "").startswith("."):
            c["domain"] = c["domain"].lstrip(".")
        if "expiry" in c:
            c["expiry"] = int(c["expiry"])
        try:
            driver.add_cookie(c)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipped cookie %s: %s", c.get("name"), exc)

    driver.refresh()
    human_delay(2, 5)


def _find_login_fields(driver):
    """Locate username/email and password fields (Instagram UI varies)."""
    from selenium.webdriver.common.by import By

    candidates = [
        (By.NAME, "username"),
        (By.NAME, "email"),
        (By.CSS_SELECTOR, "input[autocomplete='username']"),
        (By.CSS_SELECTOR, "input[type='text'][name='email']"),
    ]
    user_input = None
    for by, sel in candidates:
        els = driver.find_elements(by, sel)
        if els:
            user_input = els[0]
            break

    pass_candidates = [
        (By.NAME, "password"),
        (By.NAME, "pass"),
        (By.CSS_SELECTOR, "input[type='password']"),
    ]
    pass_input = None
    for by, sel in pass_candidates:
        els = driver.find_elements(by, sel)
        if els:
            pass_input = els[0]
            break

    return user_input, pass_input


def login_with_credentials(driver, config: ScraperConfig) -> None:
    if not config.instagram_username or not config.instagram_password:
        raise ValueError("instagram_username and instagram_password required for login")

    driver.get("https://www.instagram.com/accounts/login/")
    human_delay(3, 6)
    dismiss_cookie_banners(driver)

    wait = WebDriverWait(driver, 40)
    wait.until(lambda d: _find_login_fields(d)[0] is not None)
    user_input, pass_input = _find_login_fields(driver)
    if not user_input or not pass_input:
        raise RuntimeError("Could not locate Instagram login form fields")

    human_type(user_input, config.instagram_username)
    human_delay(0.5, 1.5)
    human_type(pass_input, config.instagram_password)
    human_delay(0.8, 2.0)

    # Submit via password field or login button
    try:
        pass_input.submit()
    except Exception:  # noqa: BLE001
        for sel in (
            "button[type='submit']",
            "button:contains('Log in')",
        ):
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            if btns:
                btns[0].click()
                break
    human_delay(4, 8)

    # Dismiss "Save login info" / notifications if shown
    for xpath in (
        "//button[contains(text(),'Not Now')]",
        "//button[contains(text(),'Not now')]",
        "//div[@role='button' and contains(text(),'Not Now')]",
    ):
        try:
            btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            btn.click()
            human_delay(1, 3)
        except TimeoutException:
            pass


def dismiss_cookie_banners(driver) -> None:
    xpaths = (
        "//button[contains(translate(., 'ALLOW ALL COOKIES', 'allow all cookies'), 'allow all cookies')]",
        "//button[contains(translate(., 'ACCEPT ALL', 'accept all'), 'accept all')]",
        "//button[contains(., 'Allow essential and optional cookies')]",
        "//button[contains(., 'Decline optional cookies')]",
    )
    for xpath in xpaths:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xpath)))
            btn.click()
            human_delay(1, 2)
            return
        except TimeoutException:
            continue


def ensure_session(driver, config: ScraperConfig) -> None:
    driver.get("https://www.instagram.com/")
    human_delay(2, 4)
    dismiss_cookie_banners(driver)

    if config.cookies_file and config.cookies_file.exists():
        logger.info("Loading cookies from %s", config.cookies_file)
        load_cookies_from_file(driver, config.cookies_file)

    if is_logged_in(driver):
        logger.info("Authenticated session detected")
        return

    if config.instagram_username and config.instagram_password:
        logger.info("Attempting credential login")
        login_with_credentials(driver, config)
        if is_logged_in(driver):
            logger.info("Login successful")
            return

    if is_challenge_page(driver):
        logger.warning(
            "Instagram challenge detected. Complete verification manually; waiting %ss",
            config.challenge_wait_seconds,
        )
        deadline = time.time() + config.challenge_wait_seconds
        while time.time() < deadline:
            if is_logged_in(driver) and not is_challenge_page(driver):
                logger.info("Challenge cleared")
                return
            human_delay(3, 5)
        raise RuntimeError("Challenge page not cleared within timeout")

    logger.warning("Proceeding without confirmed login (public data only)")
