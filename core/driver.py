"""Stealth WebDriver factory (platform-agnostic)."""

from __future__ import annotations

import logging
import os
import random
import re
import subprocess
from pathlib import Path

import undetected_chromedriver as uc
from selenium_stealth import stealth

from .config import BaseScraperConfig
from .fingerprint import BrowserFingerprint, generate_fingerprint

logger = logging.getLogger(__name__)


def is_docker() -> bool:
    return Path("/.dockerenv").exists() or os.getenv("IN_DOCKER", "").lower() in {"1", "true", "yes"}


def _apply_chrome_options(options, config: BaseScraperConfig, fp: BrowserFingerprint) -> None:
    in_docker = is_docker()
    # Prefer Xvfb virtual display in Docker; fall back to headless only without DISPLAY.
    use_headless = config.headless and not (in_docker and os.getenv("DISPLAY"))
    if use_headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-infobars")
    options.add_argument("--window-size=%d,%d" % (fp.width, fp.height))
    options.add_argument("--lang=%s" % fp.languages[0])
    options.add_argument(f"--user-agent={fp.user_agent}")

    if in_docker or config.headless:
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--remote-allow-origins=*")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--disable-background-networking")

    if config.proxy:
        options.add_argument(f"--proxy-server={config.proxy}")

    if config.user_data_dir:
        profile_path = Path(config.user_data_dir)
        profile_path.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={profile_path}")
        if config.chrome_profile:
            options.add_argument(f"--profile-directory={config.chrome_profile}")


def detect_chrome_major(chrome_binary: Path) -> int | None:
    try:
        output = subprocess.check_output(
            [str(chrome_binary), "--version"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
        )
        match = re.search(r"(\d+)\.", output)
        if match:
            return int(match.group(1))
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not detect Chrome version from %s: %s", chrome_binary, exc)
    return None


def build_driver(
    config: BaseScraperConfig,
    fingerprint: BrowserFingerprint | None = None,
    on_new_document_scripts: list[str] | None = None,
) -> uc.Chrome:
    """Build a stealth undetected-chromedriver session.

    ``on_new_document_scripts`` are platform-supplied JS strings injected via
    CDP on every new document (e.g. the network interceptor).
    """
    fp = fingerprint or generate_fingerprint(config.user_agent)

    options = uc.ChromeOptions()
    options.binary_location = str(config.chrome_binary)
    _apply_chrome_options(options, config, fp)

    in_docker = is_docker()
    driver_kwargs: dict = {
        "options": options,
        "browser_executable_path": str(config.chrome_binary),
        "use_subprocess": True,
    }
    version_main = detect_chrome_major(config.chrome_binary)
    if version_main:
        driver_kwargs["version_main"] = version_main

    chromedriver = config.chromedriver_path
    if chromedriver and Path(chromedriver).exists():
        driver_kwargs["driver_executable_path"] = str(chromedriver)
        logger.info("Using chromedriver at %s", chromedriver)
    elif in_docker:
        logger.warning("Bundled chromedriver not found at %s; letting uc auto-download", chromedriver)

    logger.info(
        "Launching Chrome (docker=%s, headless=%s, display=%s, profile=%s)",
        in_docker,
        config.headless,
        os.getenv("DISPLAY"),
        config.user_data_dir,
    )

    driver = uc.Chrome(**driver_kwargs)
    driver.set_window_size(fp.width, fp.height)

    stealth(
        driver,
        user_agent=fp.user_agent,
        languages=fp.languages,
        vendor="Google Inc.",
        platform=fp.platform,
        webgl_vendor=fp.webgl_vendor,
        renderer=fp.webgl_renderer,
        fix_hairline=True,
    )

    for script in on_new_document_scripts or []:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script})

    # Extra fingerprint hardening via CDP
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'maxTouchPoints', { get: () => %d });
            window.chrome = window.chrome || { runtime: {} };
            """ % random.choice([0, 1, 5])
        },
    )

    logger.info(
        "Stealth driver ready (%dx%d, UA=%s...)",
        fp.width,
        fp.height,
        fp.user_agent[:60],
    )
    return driver
