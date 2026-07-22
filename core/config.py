"""Base configuration shared by all platform scrapers.

Loads from YAML, environment variables and CLI overrides. Platform subclasses
declare ``env_prefix`` (e.g. ``IG`` / ``FB``), ``default_platform`` and
``output_prefix``; everything else — browser/stealth, fingerprint, timing,
recovery and the Flask API-integration block — is defined once here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, ClassVar

import yaml
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHROME = PROJECT_ROOT / ".browsers" / "chrome-linux64" / "chrome"
DEFAULT_CHROMEDRIVER = PROJECT_ROOT / ".browsers" / "chromedriver-linux64" / "chromedriver"

DOCKER_CHROME_CANDIDATES = (
    Path("/usr/bin/google-chrome-stable"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
)

_TRUE = {"1", "true", "yes"}
_NONE_TOKENS = {"none", "null", "off", ""}
_AUTO_TOKENS = {"none", "null", "auto", ""}

# Fields read from ``{PREFIX}_{SUFFIX}`` env vars.
_PREFIXED_ENV_FIELDS = (
    ("PROFILE_URL", "profile_url", "str"),
    ("POST_URLS", "post_urls", "list"),
    ("POST_LIMIT", "post_limit", "int"),
    ("OUTPUT_DIR", "output_dir", "path"),
    ("USER_AGENT", "user_agent", "str"),
    ("PROXY", "proxy", "str"),
    ("COOKIES_FILE", "cookies_file", "path"),
    ("USER_DATA_DIR", "user_data_dir", "user_data_dir"),
    ("CHROME_PROFILE", "chrome_profile", "str"),
    ("USERNAME", "login_username", "str"),
    ("PASSWORD", "login_password", "str"),
    ("HEADLESS", "headless", "bool"),
    ("CHROME_BINARY", "chrome_binary", "path"),
    ("CHROMEDRIVER_PATH", "chromedriver_path", "path_or_none"),
)

# Shared (non-prefixed) env vars for the Flask backend integration.
_SHARED_ENV_FIELDS = (
    ("SCRAPING_API_KEY", "scraping_api_key", "str"),
    ("SCRAPING_API_URL", "scraping_api_url", "str"),
    ("SCRAPING_PLATFORM", "scraping_platform", "str"),
    ("SCRAPING_BATCH_SIZE", "scraping_batch_size", "int"),
    ("SCRAPING_BATCH_DELAY_SECONDS", "scraping_batch_delay_seconds", "int"),
    ("SCRAPING_START_DATE_DAYS", "scraping_start_date_days", "int"),
    ("SCRAPING_API_MODE", "api_mode", "bool"),
)


def resolve_default_chrome() -> Path:
    if chrome := os.getenv("CHROME_BINARY"):
        return Path(chrome)
    for candidate in DOCKER_CHROME_CANDIDATES:
        if candidate.exists():
            return candidate
    return DEFAULT_CHROME


@dataclass
class BaseScraperConfig:
    # --- scrape target ---
    profile_url: str = ""
    post_urls: list[str] = field(default_factory=list)
    post_limit: int = 10
    output_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "output")

    # --- Flask API integration ---
    scraping_api_key: str = ""
    scraping_api_url: str = "http://localhost:5000"
    scraping_platform: str = ""
    scraping_batch_size: int = 20
    scraping_start_date_days: int = 2
    api_mode: bool = False
    scraping_batch_delay_seconds: int = 0

    # --- browser / stealth ---
    headless: bool = False
    chrome_binary: Path = field(default_factory=resolve_default_chrome)
    chromedriver_path: Path | None = field(default_factory=lambda: DEFAULT_CHROMEDRIVER)
    user_data_dir: str | None = None
    chrome_profile: str | None = None
    cookies_file: Path | None = None

    # --- fingerprint rotation ---
    rotate_fingerprint: bool = True
    user_agent: str | None = None
    language: str = "en-US,en"
    window_width: int = 1366
    window_height: int = 768

    # --- proxy ---
    proxy: str | None = None  # e.g. http://user:pass@host:port

    # --- login (optional — prefer cookies / profile) ---
    login_username: str | None = None
    login_password: str | None = None

    # --- timing (seconds) ---
    delay_min: float = 3.0
    delay_max: float = 12.0
    scroll_pause_min: float = 0.8
    scroll_pause_max: float = 2.5
    max_comment_scroll_rounds: int = 80
    max_idle_rounds: int = 6

    # --- recovery ---
    max_retries: int = 3
    challenge_wait_seconds: int = 120

    # --- platform hooks (overridden by subclasses) ---
    env_prefix: ClassVar[str] = "IG"
    default_platform: ClassVar[str] = "instagram"
    output_prefix: ClassVar[str] = "scrape"
    base_url: ClassVar[str] = ""
    default_cookies_filename: ClassVar[str] = ""

    def resolved_cookies_file(self) -> Path | None:
        """Cookie file to load from / save to: explicit config wins, else the
        platform default under ``cookies/`` (enables one-login-then-reuse)."""
        if self.cookies_file:
            return Path(self.cookies_file)
        if self.default_cookies_filename:
            return PROJECT_ROOT / "cookies" / self.default_cookies_filename
        return None

    # ------------------------------------------------------------------ paths
    @classmethod
    def _resolve_path(cls, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path

    # ------------------------------------------------------------------ env
    def _set_env_field(self, attr: str, raw: str, kind: str) -> None:
        if kind == "str":
            setattr(self, attr, raw)
        elif kind == "int":
            setattr(self, attr, int(raw))
        elif kind == "float":
            setattr(self, attr, float(raw))
        elif kind == "bool":
            setattr(self, attr, raw.lower() in _TRUE)
        elif kind == "list":
            setattr(self, attr, [u.strip() for u in raw.split(",") if u.strip()])
        elif kind == "path":
            setattr(self, attr, Path(raw))
        elif kind == "user_data_dir":
            setattr(self, attr, None if raw.lower() in _NONE_TOKENS else raw)
        elif kind == "path_or_none":
            setattr(self, attr, None if raw.lower() in _AUTO_TOKENS else Path(raw))

    def apply_env(self) -> "BaseScraperConfig":
        """Apply environment overrides in place."""
        prefix = self.env_prefix
        for suffix, attr, kind in _PREFIXED_ENV_FIELDS:
            raw = os.getenv(f"{prefix}_{suffix}")
            # chromedriver honours the empty string (means "auto"); others skip it.
            if raw is None:
                continue
            if raw == "" and kind not in {"user_data_dir", "path_or_none"}:
                continue
            self._set_env_field(attr, raw, kind)
        # Bare CHROME_BINARY fallback (docker / shared).
        if os.getenv(f"{prefix}_CHROME_BINARY") is None and (chrome := os.getenv("CHROME_BINARY")):
            self.chrome_binary = Path(chrome)
        for env_name, attr, kind in _SHARED_ENV_FIELDS:
            raw = os.getenv(env_name)
            if raw is None or raw == "":
                continue
            self._set_env_field(attr, raw, kind)
        return self

    # Backwards-compatible alias used by the entrypoint.
    apply_env_overrides = apply_env

    @classmethod
    def from_env(cls) -> "BaseScraperConfig":
        cfg = cls()
        cfg.apply_env()
        return cfg

    # ------------------------------------------------------------------ dict / yaml
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaseScraperConfig":
        cfg = cls()
        path_fields = {"output_dir", "chrome_binary", "chromedriver_path", "cookies_file"}
        valid = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key not in valid:
                continue
            if key in path_fields:
                if value in (None, "", "null"):
                    if key == "chromedriver_path":
                        cfg.chromedriver_path = None
                    continue
                setattr(cfg, key, cls._resolve_path(value))
                continue
            setattr(cfg, key, value)
        return cfg

    @classmethod
    def from_yaml(cls, path: Path) -> "BaseScraperConfig":
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        return cls.from_dict(raw)

    # ------------------------------------------------------------------ cli
    def merge_cli(self, **kwargs: Any) -> "BaseScraperConfig":
        for key, value in kwargs.items():
            if value is None:
                continue
            if key == "post_urls" and isinstance(value, list):
                self.post_urls = value
            elif key in {"output_dir", "cookies_file"}:
                setattr(self, key, Path(value))
            elif hasattr(self, key):
                setattr(self, key, value)
        return self

    # ------------------------------------------------------------------ validation
    def validate(self) -> None:
        if not self.scraping_platform:
            self.scraping_platform = self.default_platform
        if not self.api_mode and not self.profile_url and not self.post_urls:
            raise ValueError(
                "Provide profile_url (--profile-url), post_url(s) (--post-url / post_urls in "
                "config), or enable api_mode."
            )
        if not self.chrome_binary.exists():
            raise FileNotFoundError(
                f"Chrome binary not found at {self.chrome_binary}. "
                "Install Chrome or set chrome_binary in config."
            )
        self.output_dir.mkdir(parents=True, exist_ok=True)
