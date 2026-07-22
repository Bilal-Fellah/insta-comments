"""Shared cookie load/save so a one-time login can be reused across runs."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_ALLOWED = {"name", "value", "domain", "path", "expiry", "secure", "httpOnly", "sameSite"}


def load_cookies_from_file(driver, cookies_file: Path, home_url: str) -> None:
    with Path(cookies_file).open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    if isinstance(data, dict) and "cookies" in data:
        cookies = data["cookies"]
    elif isinstance(data, list):
        cookies = data
    else:
        raise ValueError("Unsupported cookie file format. Export JSON array or {cookies: [...]}.")

    driver.get(home_url)
    from core.human import human_delay

    human_delay(2, 4)

    for cookie in cookies:
        c = {k: cookie[k] for k in cookie if k in _ALLOWED}
        if "name" not in c or "value" not in c:
            continue
        if c.get("domain", "").startswith("."):
            c["domain"] = c["domain"].lstrip(".")
        if "expiry" in c:
            try:
                c["expiry"] = int(c["expiry"])
            except (TypeError, ValueError):
                c.pop("expiry", None)
        try:
            driver.add_cookie(c)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skipped cookie %s: %s", c.get("name"), exc)

    driver.refresh()
    human_delay(2, 5)


def save_cookies_to_file(driver, cookies_file: Path) -> None:
    path = Path(cookies_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = driver.get_cookies()
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        logger.info("Saved %d cookies to %s", len(data), path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not save cookies to %s: %s", path, exc)
