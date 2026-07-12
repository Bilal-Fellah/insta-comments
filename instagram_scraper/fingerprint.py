"""Browser fingerprint rotation helpers."""

from __future__ import annotations

import platform
import random
from dataclasses import dataclass

LINUX_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
MAC_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

DESKTOP_USER_AGENTS = [LINUX_UA, WINDOWS_UA, MAC_UA]

RESOLUTIONS = [
    (1920, 1080),
    (1366, 768),
    (1536, 864),
    (1440, 900),
    (1280, 720),
    (2560, 1440),
]

LANGUAGES = [
    ["en-US", "en"],
    ["en-GB", "en"],
    ["en-US", "en", "es"],
    ["de-DE", "de", "en"],
    ["fr-FR", "fr", "en"],
]

WEBGL_VENDORS = [
    ("Intel Inc.", "Intel Iris OpenGL Engine"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Series Direct3D11 vs_5_0 ps_5_0)"),
]

PLATFORMS = ["Win32", "Linux x86_64", "MacIntel"]


def _host_platform() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "MacIntel"
    if system == "windows":
        return "Win32"
    return "Linux x86_64"


def _default_user_agent() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return MAC_UA
    if system == "windows":
        return WINDOWS_UA
    return LINUX_UA


@dataclass
class BrowserFingerprint:
    user_agent: str
    languages: list[str]
    width: int
    height: int
    platform: str
    webgl_vendor: str
    webgl_renderer: str


_ua = None  # desktop-only list below


def generate_fingerprint(preferred_ua: str | None = None) -> BrowserFingerprint:
    width, height = random.choice(RESOLUTIONS)
    vendor, renderer = random.choice(WEBGL_VENDORS)
    host_platform = _host_platform()
    ua = preferred_ua or _default_user_agent()
    # Keep platform aligned with UA to avoid Instagram "useragent mismatch" errors.
    if "Linux" in ua:
        host_platform = "Linux x86_64"
    elif "Windows" in ua:
        host_platform = "Win32"
    elif "Macintosh" in ua:
        host_platform = "MacIntel"
    return BrowserFingerprint(
        user_agent=ua,
        languages=random.choice(LANGUAGES),
        width=width,
        height=height,
        platform=host_platform,
        webgl_vendor=vendor,
        webgl_renderer=renderer,
    )
