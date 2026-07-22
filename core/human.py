"""Human-like interaction simulation."""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selenium.webdriver.remote.webdriver import WebDriver


def human_delay(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _bezier(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    u = 1 - t
    return u**3 * p0 + 3 * u**2 * t * p1 + 3 * u * t**2 * p2 + t**3 * p3


def move_mouse_human(driver: WebDriver, start: tuple[int, int], end: tuple[int, int], steps: int = 25) -> None:
    sx, sy = start
    ex, ey = end
    cp1 = (sx + random.randint(-80, 80), sy + random.randint(-60, 60))
    cp2 = (ex + random.randint(-80, 80), ey + random.randint(-60, 60))
    for i in range(steps + 1):
        t = i / steps
        x = int(_bezier(t, sx, cp1[0], cp2[0], ex))
        y = int(_bezier(t, sy, cp1[1], cp2[1], ey))
        driver.execute_script(
            """
            const ev = new MouseEvent('mousemove', {
                bubbles: true, cancelable: true, view: window,
                clientX: arguments[0], clientY: arguments[1]
            });
            document.dispatchEvent(ev);
            """,
            x,
            y,
        )
        time.sleep(random.uniform(0.005, 0.02))


def random_mouse_wander(driver: WebDriver, width: int, height: int) -> None:
    start = (random.randint(80, max(120, width - 120)), random.randint(80, max(120, height - 120)))
    end = (random.randint(80, max(120, width - 120)), random.randint(80, max(120, height - 120)))
    move_mouse_human(driver, start, end, steps=random.randint(15, 35))


def human_scroll(driver: WebDriver, container_selector: str | None = None, direction: int = 1) -> None:
    """Scroll with variable speed and occasional back-scroll."""
    amount = random.randint(280, 900) * direction
    if random.random() < 0.15:
        amount = -int(amount * random.uniform(0.2, 0.5))

    if container_selector:
        driver.execute_script(
            """
            const el = document.querySelector(arguments[0]);
            if (el) el.scrollTop += arguments[1];
            """,
            container_selector,
            amount,
        )
    else:
        driver.execute_script("window.scrollBy(0, arguments[0]);", amount)


def human_type(element, text: str) -> None:
    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(0.05, 0.18))
        if random.random() < 0.04:
            time.sleep(random.uniform(0.2, 0.6))
